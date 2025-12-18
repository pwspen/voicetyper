from __future__ import annotations

import signal
import sys
import threading
from pathlib import Path

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
    from gi.repository import GLib, Gtk, Notify
except Exception:  # pragma: no cover - only runs when deps are missing
    print(
        "Voicetyper tray requires GTK/AppIndicator bindings in the current Python "
        "(python3-gi, and gir1.2-ayatanaappindicator3-0.1 or gir1.2-appindicator3-0.1).",
        file=sys.stderr,
    )
    print(
        "Tip: use the system Python that has gi installed, e.g. UV_PYTHON=/usr/bin/python3 uv sync --python /usr/bin/python3",
        file=sys.stderr,
    )
    sys.exit(1)

from voicetyper.audio.devices import InputDevice, default_input_device_index, list_input_devices
from voicetyper.config import DEFAULT_CONFIG, AppConfig
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
        Notify.init("Voicetyper")
        self._indicator = self._build_indicator()
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

        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", self._quit)
        menu.append(quit_item)

        menu.show_all()
        indicator.set_menu(menu)
        return indicator

    def _start_state_timer(self):
        GLib.timeout_add(500, self._refresh_state)

    def _notify(self, message: str):
        try:
            note = Notify.Notification.new("Voicetyper", message, None)
            note.show()
        except Exception:
            # Notification failures should not crash the app.
            pass

    def _toggle_listening(self, _menuitem):
        new_state = not self.controller.enabled
        self.controller.set_enabled(new_state)
        status = "Enabled" if new_state else "Disabled"
        self._notify(f"{status} voice typing on {self.device.name}")
        self._refresh_state()

    def _refresh_state(self):
        enabled = self.controller.enabled
        streaming = self.controller.listening
        icon = self.icon_on if enabled else self.icon_off
        description = "Listening" if streaming else ("Ready" if enabled else "Disabled")
        try:
            self._indicator.set_icon_full(icon, description)
        except Exception:
            # Fall back to set_icon for theme names.
            self._indicator.set_icon(icon)
        self._indicator.set_title(f"Voicetyper: {description}")
        self.toggle_item.set_label("Disable Listening" if enabled else "Enable Listening")
        return True

    def _quit(self, _menuitem=None):
        self.controller.set_enabled(False)
        Gtk.main_quit()

    def run(self):
        signal.signal(signal.SIGINT, lambda *_args: self._quit())
        signal.signal(signal.SIGTERM, lambda *_args: self._quit())
        self._notify(f"Voicetyper ready on {self.device.name}")
        Gtk.main()


def main():
    config = DEFAULT_CONFIG
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
