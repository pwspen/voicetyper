from __future__ import annotations

import signal
import sys
import threading
from pathlib import Path
import time

try:
    import gi

    gi.require_version("Gtk", "3.0")
    try:
        gi.require_version("AppIndicator3", "0.1")
        from gi.repository import AppIndicator3 as IndicatorLib
    except (ValueError, ImportError):
        # Ubuntu/Mint often ship ayatana instead of appindicator.
        gi.require_version("AyatanaAppIndicator3", "0.1")
        from gi.repository import AyatanaAppIndicator3 as IndicatorLib
    gi.require_version("Notify", "0.7")
    gi.require_version("Keybinder", "3.0")
    from gi.repository import GLib, Gtk, Notify, Keybinder, Gdk
except Exception:  # pragma: no cover - only runs when deps are missing
    print(
        "Voicetyper tray requires GTK/AppIndicator/Keybinder bindings in the current Python "
        "(python3-gi, gir1.2-ayatanaappindicator3-0.1 or gir1.2-appindicator3-0.1, and gir1.2-keybinder-3.0).",
        file=sys.stderr,
    )
    print(
        "Tip: use the system Python that has gi installed, e.g. UV_PYTHON=/usr/bin/python3 uv sync --python /usr/bin/python3",
        file=sys.stderr,
    )
    sys.exit(1)

from voicetyper.audio.devices import InputDevice, default_input_device_index, list_input_devices
from voicetyper.config import load_config, AppConfig, KeywordAction
from voicetyper.controller import VoiceController
from voicetyper.logging_utils import DebugSink
from voicetyper.stt.speechmatics_client import SpeechmaticsBackend


def _find_icon(name_candidates: list[str | Path], fallback: str) -> str:
    for candidate in name_candidates:
        path = Path(candidate)
        if path.exists():
            return str(path)
        if isinstance(candidate, str) and path.suffix == "" and not path.is_absolute():
            # Allow themed icon names (e.g., microphone-sensitivity-high).
            return candidate
    return fallback


def _select_device() -> InputDevice:
    devices = list_input_devices()
    if not devices:
        raise RuntimeError("No input devices detected.")
    default_idx = default_input_device_index()
    if default_idx is not None:
        for dev in devices:
            if dev.index == default_idx:
                return dev
    return devices[0]


class TrayApp:
    def __init__(self, config: AppConfig):
        self.config = config
        self.device = _select_device()
        self._debug_lines: list[str] = []
        self._lock = threading.Lock()
        self.sink = DebugSink(
            enabled=config.debug, log_path=Path(config.debug_log_path), buffer=self._debug_lines, lock=self._lock
        )
        self.controller = VoiceController(
            config,
            backend_factory=lambda: SpeechmaticsBackend(config, log_fn=self.sink.info),
            device=self.device,
            sink=self.sink,
        )
        repo_root = Path(__file__).resolve().parent.parent
        assets = repo_root / "assets"
        self.icon_on = _find_icon(
            [assets / "mic-on.png", assets / "mic-on.svg", "microphone-sensitivity-high"],
            fallback="audio-input-microphone-symbolic",
        )
        self.icon_off = _find_icon(
            [assets / "mic-off.png", assets / "mic-off.svg", "microphone-sensitivity-muted"],
            fallback="microphone-disabled-symbolic",
        )
        self.icon_speaking = _find_icon(
            ["media-record", "audio-volume-high"],
            fallback="emblem-sound",
        )
        Notify.init("Voicetyper")
        self._indicator = self._build_indicator()
        self._hotkey_bound = False
        self._init_hotkey()
        self._last_icon = None
        self._start_state_timer()

    def _build_indicator(self):
        indicator = IndicatorLib.Indicator.new(
            "voicetyper-tray", self.icon_off, IndicatorLib.IndicatorCategory.APPLICATION_STATUS
        )
        indicator.set_status(IndicatorLib.IndicatorStatus.ACTIVE)
        indicator.set_title("Voicetyper")

        menu = Gtk.Menu()

        self.toggle_item = Gtk.MenuItem(label="Enable Listening")
        self.toggle_item.connect("activate", self._toggle_listening)
        menu.append(self.toggle_item)

        settings_item = Gtk.MenuItem(label="Settings...")
        settings_item.connect("activate", self._show_settings_dialog)
        menu.append(settings_item)

        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", self._quit)
        menu.append(quit_item)

        menu.show_all()
        indicator.set_menu(menu)
        return indicator

    def _start_state_timer(self):
        GLib.timeout_add(500, self._refresh_state)

    def _notify(self, message: str, force: bool = False):
        """
        Send notification if enabled or forced.

        Args:
            message: Notification message
            force: If True, show notification regardless of config (for errors)
        """
        if not force and not self.config.notifications_enabled:
            return

        try:
            note = Notify.Notification.new("Voicetyper", message, None)
            note.show()
        except Exception:
            # Notification failures should not crash the app.
            pass

    def _init_hotkey(self):
        """Initialize and bind global hotkey if configured."""
        if not self.config.hotkey_toggle_listening:
            return

        try:
            Keybinder.init()
            success = Keybinder.bind(
                self.config.hotkey_toggle_listening,
                self._on_hotkey_pressed,
                None
            )

            if success:
                self._hotkey_bound = True
                self.sink.info(f"Hotkey bound: {self.config.hotkey_toggle_listening}")
            else:
                msg = f"Could not bind hotkey '{self.config.hotkey_toggle_listening}' - may be in use by another app"
                print(f"Warning: {msg}", file=sys.stderr)
                self._notify(msg, force=True)
        except Exception as exc:
            msg = f"Hotkey initialization failed: {exc}"
            print(f"Warning: {msg}", file=sys.stderr)

    def _on_hotkey_pressed(self, keystring, user_data):
        """Callback for global hotkey press (runs on different thread)."""
        GLib.idle_add(self._toggle_listening_from_hotkey)

    def _toggle_listening_from_hotkey(self):
        """Toggle listening state from hotkey (called on GTK main thread)."""
        self._toggle_listening(None)
        return False  # Don't repeat callback

    def _toggle_listening(self, _menuitem):
        new_state = not self.controller.enabled
        self.controller.set_enabled(new_state)
        status = "Enabled" if new_state else "Disabled"
        self._notify(f"{status} voice typing on {self.device.name}")
        self._refresh_state()

    def _refresh_state(self):
        enabled = self.controller.enabled
        streaming = self.controller.listening

        # Three-state icon selection
        if not enabled:
            icon = self.icon_off
            description = "Disabled"
        elif streaming:
            icon = self.icon_speaking
            description = "Speaking"
        else:
            icon = self.icon_on
            description = "Ready"

        # Only update icon if it actually changed
        if icon != self._last_icon:
            try:
                self._indicator.set_icon_full(icon, description)
            except Exception:
                # Fall back to set_icon for theme names.
                self._indicator.set_icon(icon)
            self._last_icon = icon

        self._indicator.set_title(f"Voicetyper: {description}")
        self.toggle_item.set_label("Disable Listening" if enabled else "Enable Listening")
        return True

    def _show_settings_dialog(self, _menuitem):
        """Show settings dialog and apply changes if saved."""
        dialog = Gtk.Dialog(
            title="Voicetyper Settings",
            parent=None,
            flags=Gtk.DialogFlags.MODAL,
        )
        dialog.set_default_size(500, 300)
        dialog.set_border_width(10)

        content = dialog.get_content_area()
        content.set_spacing(15)

        # Create grid layout
        grid = Gtk.Grid()
        grid.set_row_spacing(10)
        grid.set_column_spacing(10)
        grid.set_margin_start(10)
        grid.set_margin_end(10)

        # Notifications section
        row = 0
        notif_label = Gtk.Label(label="Notifications:")
        notif_label.set_halign(Gtk.Align.START)
        grid.attach(notif_label, 0, row, 1, 1)

        notif_switch = Gtk.Switch()
        notif_switch.set_active(self.config.notifications_enabled)
        notif_switch.set_halign(Gtk.Align.START)
        grid.attach(notif_switch, 1, row, 1, 1)

        # Hotkey section
        row += 1
        hotkey_label = Gtk.Label(label="Global Hotkey:")
        hotkey_label.set_halign(Gtk.Align.START)
        grid.attach(hotkey_label, 0, row, 1, 1)

        hotkey_button = Gtk.Button()
        current_hotkey = self.config.hotkey_toggle_listening or "None"
        hotkey_button.set_label(current_hotkey)
        hotkey_button.set_halign(Gtk.Align.START)

        # Store hotkey capture state
        hotkey_data = {"capturing": False, "new_hotkey": current_hotkey}
        hotkey_button.connect("clicked", self._on_hotkey_button_clicked, hotkey_data)
        grid.attach(hotkey_button, 1, row, 1, 1)

        # API Key section
        row += 1
        api_label = Gtk.Label(label="API Key:")
        api_label.set_halign(Gtk.Align.START)
        grid.attach(api_label, 0, row, 1, 1)

        api_entry = Gtk.Entry()
        api_entry.set_text(self.config.api_key or "")
        api_entry.set_placeholder_text("Enter Speechmatics API key")
        api_entry.set_width_chars(40)
        grid.attach(api_entry, 1, row, 1, 1)

        # Keyword actions section
        row += 1
        keyword_label = Gtk.Label(label="Keyword actions:")
        keyword_label.set_halign(Gtk.Align.START)
        grid.attach(keyword_label, 0, row, 1, 1)

        keyword_rows: list[dict] = []
        keywords_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        keywords_box.set_hexpand(True)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_min_content_height(180)
        scrolled.add(keywords_box)
        grid.attach(scrolled, 1, row, 1, 2)

        self._build_keyword_rows(keywords_box, keyword_rows)

        add_keyword_button = Gtk.Button(label="Add keyword")
        add_keyword_button.set_halign(Gtk.Align.START)
        add_keyword_button.connect("clicked", lambda *_args: self._add_keyword_row(keywords_box, keyword_rows, None, False))
        grid.attach(add_keyword_button, 1, row + 2, 1, 1)

        content.pack_start(grid, True, True, 0)

        # Add Save/Cancel buttons
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Save", Gtk.ResponseType.OK)

        dialog.show_all()
        response = dialog.run()

        if response == Gtk.ResponseType.OK:
            # Collect new values
            new_notifications = notif_switch.get_active()
            new_hotkey = hotkey_data["new_hotkey"]
            if new_hotkey == "None":
                new_hotkey = None
            new_api_key = api_entry.get_text().strip() or None
            new_keyword_actions = self._collect_keyword_actions(keyword_rows)

            # Apply changes
            self._apply_settings(new_notifications, new_hotkey, new_api_key, new_keyword_actions)

        dialog.destroy()

    def _on_hotkey_button_clicked(self, button, hotkey_data):
        """Handle hotkey button click to start key capture."""
        if hotkey_data["capturing"]:
            return

        hotkey_data["capturing"] = True
        button.set_label("Press key combination...")

        # Create capture window
        capture_window = Gtk.Window(title="Capture Hotkey")
        capture_window.set_default_size(300, 100)
        capture_window.set_modal(True)
        capture_window.set_type_hint(Gdk.WindowTypeHint.DIALOG)

        label = Gtk.Label(label="Press the key combination you want to use")
        label.set_margin_top(30)
        label.set_margin_bottom(30)
        capture_window.add(label)

        def on_key_press(widget, event):
            # Get key and modifiers
            keyval = event.keyval
            state = event.state

            # Ignore standalone modifier keys
            if keyval in (Gdk.KEY_Control_L, Gdk.KEY_Control_R,
                          Gdk.KEY_Alt_L, Gdk.KEY_Alt_R,
                          Gdk.KEY_Shift_L, Gdk.KEY_Shift_R,
                          Gdk.KEY_Super_L, Gdk.KEY_Super_R):
                return True

            # Convert to GTK accelerator string
            accelerator = Gtk.accelerator_name(keyval, state)

            if accelerator:
                hotkey_data["new_hotkey"] = accelerator
                button.set_label(accelerator)

            hotkey_data["capturing"] = False
            capture_window.destroy()
            return True

        capture_window.connect("key-press-event", on_key_press)

        def on_destroy(widget):
            hotkey_data["capturing"] = False

        capture_window.connect("destroy", on_destroy)
        capture_window.show_all()

    def _build_keyword_rows(self, container: Gtk.Box, rows: list[dict]):
        """Create UI rows for keyword actions, keeping one force-end row fixed."""
        actions = list(self.config.keyword_actions or [])
        force_action = next((a for a in actions if a.force_end), KeywordAction(word="", keys=[], force_end=True))
        non_force_actions = [a for a in actions if not a.force_end]

        # Fixed force-end row (cannot remove, force flag locked on)
        self._add_keyword_row(container, rows, force_action, is_force_row=True)

        # Other rows remain fully editable/removable
        if non_force_actions:
            for action in non_force_actions:
                self._add_keyword_row(container, rows, action, is_force_row=False)
        else:
            # Provide one editable row to start with
            self._add_keyword_row(container, rows, KeywordAction(word="", keys=[], force_end=False), is_force_row=False)

        container.show_all()

    def _add_keyword_row(
        self,
        container: Gtk.Box,
        rows: list[dict],
        action: KeywordAction | None,
        is_force_row: bool,
    ):
        row_box = Gtk.Box(spacing=6)
        keyword_entry = Gtk.Entry()
        keyword_entry.set_placeholder_text("Keyword (spoken)")
        if action and action.word:
            keyword_entry.set_text(action.word)
        keyword_entry.set_width_chars(18)

        binding = ""
        if action and action.keys:
            binding = action.keys[0]

        capture_button = Gtk.Button(label=binding or "Set keys")
        force_check = Gtk.CheckButton(label="Force end")
        force_check.set_active(bool(action.force_end) if action else False)
        force_check.set_sensitive(not is_force_row)

        remove_button = Gtk.Button(label="Remove")
        remove_button.set_sensitive(not is_force_row)

        row_data = {
            "box": row_box,
            "entry": keyword_entry,
            "capture_button": capture_button,
            "force_check": force_check,
            "binding": binding,
            "is_force": is_force_row,
        }

        capture_button.connect("clicked", lambda _btn: self._open_keyword_capture(row_data))
        if not is_force_row:
            remove_button.connect("clicked", lambda _btn: self._remove_keyword_row(container, rows, row_data))

        row_box.pack_start(keyword_entry, False, False, 0)
        row_box.pack_start(capture_button, False, False, 0)
        row_box.pack_start(force_check, False, False, 0)
        row_box.pack_start(remove_button, False, False, 0)

        rows.append(row_data)
        container.pack_start(row_box, False, False, 0)
        container.show_all()

    def _remove_keyword_row(self, container: Gtk.Box, rows: list[dict], row_data: dict):
        if row_data.get("is_force"):
            return
        if row_data in rows:
            rows.remove(row_data)
        box = row_data.get("box")
        if box:
            container.remove(box)
        container.show_all()

    def _open_keyword_capture(self, row_data: dict):
        """Capture key presses for keyword binding."""
        capture_window = Gtk.Window(title="Capture key press")
        capture_window.set_default_size(320, 120)
        capture_window.set_modal(True)
        capture_window.set_type_hint(Gdk.WindowTypeHint.DIALOG)

        label = Gtk.Label(label="Press the key combination to send when this keyword is spoken")
        label.set_margin_top(30)
        label.set_margin_bottom(30)
        capture_window.add(label)

        def on_key_press(widget, event):
            result = self._binding_from_event(event)
            if not result:
                return True
            display, binding = result
            row_data["binding"] = binding
            button: Gtk.Button = row_data["capture_button"]
            button.set_label(display or binding or "Set keys")
            capture_window.destroy()
            return True

        capture_window.connect("key-press-event", on_key_press)
        capture_window.show_all()

    def _binding_from_event(self, event) -> tuple[str, str] | None:
        """Translate a GTK key event into (display, xdotool_binding)."""
        keyval = event.keyval
        state = event.state

        # Ignore pure modifier presses
        if keyval in (
            Gdk.KEY_Control_L, Gdk.KEY_Control_R,
            Gdk.KEY_Alt_L, Gdk.KEY_Alt_R,
            Gdk.KEY_Shift_L, Gdk.KEY_Shift_R,
            Gdk.KEY_Super_L, Gdk.KEY_Super_R,
        ):
            return None

        display = Gtk.accelerator_name(keyval, state)
        base = Gdk.keyval_name(keyval) or ""
        if not base:
            return None

        mods = []
        if state & Gdk.ModifierType.CONTROL_MASK:
            mods.append("ctrl")
        if state & Gdk.ModifierType.SHIFT_MASK:
            mods.append("shift")
        if state & Gdk.ModifierType.MOD1_MASK:
            mods.append("alt")
        if state & Gdk.ModifierType.SUPER_MASK:
            mods.append("super")

        binding_parts = mods + [base]
        binding = "+".join(binding_parts)
        return display, binding

    def _collect_keyword_actions(self, rows: list[dict]) -> list[KeywordAction]:
        """Gather keyword actions from UI rows."""
        actions: list[KeywordAction] = []
        for row in rows:
            word = row["entry"].get_text().strip()
            if not word:
                continue
            binding = row.get("binding") or ""
            keys = [binding] if binding else []
            force_end = row["force_check"].get_active()
            actions.append(KeywordAction(word=word, keys=keys, force_end=force_end))

        if not actions:
            # Ensure at least one action exists
            actions.append(KeywordAction(word="enter", keys=["KP_Enter"], force_end=True))
        return actions

    def _apply_settings(
        self,
        notifications_enabled: bool,
        hotkey: str | None,
        api_key: str | None,
        keyword_actions: list[KeywordAction],
    ):
        """Apply new settings and persist to config file."""
        from voicetyper.config import save_config

        # Track changes
        hotkey_changed = hotkey != self.config.hotkey_toggle_listening
        api_key_changed = api_key != self.config.api_key
        keywords_changed = keyword_actions != self.config.keyword_actions

        # Update config
        self.config.notifications_enabled = notifications_enabled
        self.config.hotkey_toggle_listening = hotkey
        self.config.api_key = api_key
        self.config.keyword_actions = keyword_actions

        # Save to disk
        if not save_config(self.config):
            self._notify("Failed to save settings", force=True)
            return

        # Apply hotkey changes
        if hotkey_changed:
            self._update_hotkey(hotkey)

        # Apply keyword changes by restarting if currently enabled
        if keywords_changed and self.controller.enabled:
            self.controller.set_enabled(False)
            time.sleep(0.2)
            self.controller.set_enabled(True)

        # Handle API key changes
        if api_key_changed:
            self._handle_api_key_change(api_key)

        self._notify("Settings saved successfully")

    def _update_hotkey(self, new_hotkey: str | None):
        """Update global hotkey binding."""
        # Unbind old hotkey
        if self._hotkey_bound and self.config.hotkey_toggle_listening:
            try:
                Keybinder.unbind(self.config.hotkey_toggle_listening)
                self._hotkey_bound = False
                self.sink.info(f"Hotkey unbound: {self.config.hotkey_toggle_listening}")
            except Exception as exc:
                self.sink.info(f"Failed to unbind hotkey: {exc}")

        # Bind new hotkey
        if new_hotkey:
            try:
                success = Keybinder.bind(new_hotkey, self._on_hotkey_pressed, None)
                if success:
                    self._hotkey_bound = True
                    self.sink.info(f"Hotkey bound: {new_hotkey}")
                else:
                    msg = f"Could not bind hotkey '{new_hotkey}' - may be in use by another app"
                    self.sink.info(msg)
                    self._notify(msg, force=True)
            except Exception as exc:
                msg = f"Hotkey binding failed: {exc}"
                self.sink.info(msg)
                self._notify(msg, force=True)

    def _handle_api_key_change(self, new_api_key: str | None):
        """Handle API key change by restarting backend if needed."""
        if not new_api_key:
            self._notify("Warning: No API key set. Voice typing will not work.", force=True)
            return

        # If currently listening, restart controller to use new key
        was_enabled = self.controller.enabled
        if was_enabled:
            self.controller.set_enabled(False)
            # Brief pause for clean shutdown
            import time
            time.sleep(0.5)
            self.controller.set_enabled(True)
            self._notify("Backend restarted with new API key")

    def _quit(self, _menuitem=None):
        # Unbind hotkey
        if self._hotkey_bound and self.config.hotkey_toggle_listening:
            try:
                Keybinder.unbind(self.config.hotkey_toggle_listening)
            except Exception:
                pass
        self.controller.set_enabled(False)
        Gtk.main_quit()

    def run(self):
        signal.signal(signal.SIGINT, lambda *_args: self._quit())
        signal.signal(signal.SIGTERM, lambda *_args: self._quit())
        self._notify(f"Voicetyper ready on {self.device.name}")
        Gtk.main()


def main():
    config = load_config()
    api_key = config.resolve_api_key()
    if not api_key:
        print("Missing SPEECHMATICS_API_KEY environment variable or config.api_key.", file=sys.stderr)
        return 1
    try:
        app = TrayApp(config)
    except Exception as exc:
        print(f"Failed to start tray: {exc}", file=sys.stderr)
        Notify.init("Voicetyper")
        try:
            note = Notify.Notification.new("Voicetyper", f"Tray failed: {exc}", None)
            note.show()
        except Exception:
            pass
        return 1
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
