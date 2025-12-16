from __future__ import annotations

import curses
import time
from typing import Callable, List, Optional

from voicetyper.audio.devices import InputDevice


class TerminalUI:
    def __init__(self):
        self.stdscr: Optional[curses.window] = None

    def __enter__(self):
        self.stdscr = curses.initscr()
        curses.noecho()
        curses.cbreak()
        self.stdscr.keypad(True)
        curses.curs_set(0)
        self.stdscr.nodelay(True)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.stdscr:
            self.stdscr.keypad(False)
        curses.echo()
        curses.nocbreak()
        curses.endwin()

    def draw_mic_selection(self, devices: List[InputDevice], selected_idx: int, level: float):
        assert self.stdscr
        self.stdscr.clear()
        self.stdscr.addstr(0, 0, "Select microphone (up/down, Enter to confirm):")
        for i, dev in enumerate(devices):
            marker = ">" if i == selected_idx else " "
            level_bar = self._meter_bar(level if i == selected_idx else 0.0, width=20)
            self.stdscr.addstr(2 + i, 0, f"{marker} [{dev.index}] {dev.name}  {level_bar}")
        self.stdscr.refresh()

    def draw_status(
        self,
        listening_enabled: bool,
        streaming: bool,
        listen_hotkey: str,
        device: InputDevice,
        silence_timeout: float,
        prefer_partials: bool,
        debug_enabled: bool,
        debug_lines: list[str],
    ):
        assert self.stdscr
        self.stdscr.erase()
        max_rows, max_cols = self.stdscr.getmaxyx()
        max_cols = max(max_cols, 1)

        def safe_add(row: int, text: str):
            if row >= max_rows:
                return
            clipped = text[: max_cols - 1] if max_cols > 1 else ""
            try:
                self.stdscr.addstr(row, 0, clipped)
            except curses.error:
                pass

        safe_add(0, "VoiceTyper")
        safe_add(1, f"Listening enabled: {'YES' if listening_enabled else 'no'}    Streaming: {'YES' if streaming else 'no'}")
        safe_add(2, f"Hotkey: toggle listening = {listen_hotkey}")
        safe_add(3, f"Mic: [{device.index}] {device.name}")
        safe_add(4, f"Silence timeout: {silence_timeout:.2f}s  Partials->xdotool: {prefer_partials}")
        safe_add(6, "Press 'q' to quit.")
        if debug_enabled:
            safe_add(8, "Debug:")
            start_row = 9
            available_rows = max_rows - start_row
            lines_to_show = debug_lines[-available_rows:] if available_rows > 0 else []
            for i, line in enumerate(lines_to_show):
                safe_add(start_row + i, line)
        self.stdscr.noutrefresh()
        curses.doupdate()

    def get_key(self, delay: float = 0.1) -> int | None:
        assert self.stdscr
        self.stdscr.nodelay(True)
        key = self.stdscr.getch()
        if key == curses.ERR:
            time.sleep(delay)
            return None
        return key

    @staticmethod
    def _meter_bar(level: float, width: int = 10) -> str:
        clamped = max(0.0, min(1.0, level))
        filled = int(clamped * width)
        return "[" + "#" * filled + "-" * (width - filled) + "]"
