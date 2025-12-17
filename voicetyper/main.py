from __future__ import annotations

import curses
import sys
import threading
import time
from pathlib import Path

from voicetyper.audio.capture import AudioLevelMeter, MicrophoneStream
from voicetyper.audio.devices import InputDevice, default_input_device_index, list_input_devices
from voicetyper.config import DEFAULT_CONFIG, AppConfig
from voicetyper.controller import VoiceController
from voicetyper.logging_utils import DebugSink
from voicetyper.stt.speechmatics_client import SpeechmaticsBackend
from voicetyper.ui.terminal import TerminalUI


def select_device(ui: TerminalUI, devices: list[InputDevice], config: AppConfig) -> InputDevice:
    if not devices:
        raise RuntimeError("No input devices detected.")

    selected = 0
    default_idx = default_input_device_index()
    if default_idx is not None:
        for i, dev in enumerate(devices):
            if dev.index == default_idx:
                selected = i
                break

    level_meter = AudioLevelMeter()
    stream = MicrophoneStream(
        device_index=devices[selected].index,
        sample_rate=config.sample_rate,
        channels=1,
        level_meter=level_meter,
    )
    stream.start()

    try:
        while True:
            ui.draw_mic_selection(devices, selected, level_meter.level())
            key = ui.get_key()
            if key is None:
                continue
            if key in (curses.KEY_UP, ord("k")):
                selected = (selected - 1) % len(devices)
                stream.stop()
                stream = MicrophoneStream(
                    device_index=devices[selected].index,
                    sample_rate=config.sample_rate,
                    channels=1,
                    level_meter=level_meter,
                )
                stream.start()
            elif key in (curses.KEY_DOWN, ord("j")):
                selected = (selected + 1) % len(devices)
                stream.stop()
                stream = MicrophoneStream(
                    device_index=devices[selected].index,
                    sample_rate=config.sample_rate,
                    channels=1,
                    level_meter=level_meter,
                )
                stream.start()
            elif key in (curses.KEY_ENTER, 10, 13):
                stream.stop()
                return devices[selected]
    finally:
        stream.stop()


def run_app(config: AppConfig):
    devices = list_input_devices()
    with TerminalUI() as ui:
        device = select_device(ui, devices, config)
        debug_lines: list[str] = []
        debug_lock = threading.Lock()
        log_path = Path(config.debug_log_path)
        sink = DebugSink(enabled=config.debug, log_path=log_path, buffer=debug_lines, lock=debug_lock)

        controller = VoiceController(
            config,
            backend_factory=lambda: SpeechmaticsBackend(config, log_fn=sink.info),
            device=device,
            sink=sink,
        )

        last_render = 0.0
        last_state = None
        while True:
            state = (controller.enabled, controller.listening)
            now = time.time()
            should_render = (now - last_render) >= 0.1 or state != last_state

            if should_render:
                lines_snapshot = sink.snapshot(limit=80)
                ui.draw_status(
                    listening_enabled=controller.enabled,
                    streaming=controller.listening,
                    listen_hotkey=config.listen_hotkey,
                    device=device,
                    silence_timeout=config.silence_timeout,
                    prefer_partials=config.prefer_partials,
                    debug_enabled=config.debug,
                    debug_lines=lines_snapshot,
                )
                last_render = now
                last_state = state

            key = ui.get_key()
            if key is None:
                continue
            if key in (ord("q"), 27):  # q or ESC
                controller.set_enabled(False)
                break
            try:
                char = chr(key)
            except ValueError:
                char = ""
            if char == config.listen_hotkey:
                controller.toggle_enabled()


def main():
    config = DEFAULT_CONFIG
    if not config.resolve_api_key():
        print("Missing SPEECHMATICS_API_KEY environment variable or config.api_key.", file=sys.stderr)
        sys.exit(1)
    run_app(config)


if __name__ == "__main__":
    main()
