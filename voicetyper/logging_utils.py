from __future__ import annotations

import threading
import time
import traceback
from pathlib import Path
from typing import List


class DebugSink:
    """
    Collects debug lines for UI and optionally writes errors/tracebacks to a file.
    """

    def __init__(self, enabled: bool, log_path: Path, buffer: List[str], lock: threading.Lock):
        self.enabled = enabled
        self.log_path = log_path
        self.buffer = buffer
        self.lock = lock
        if self.enabled:
            try:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass

    def _timestamp(self) -> str:
        return time.strftime("%H:%M:%S")

    def _append_ui(self, msg: str):
        line = f"[{self._timestamp()}] {msg}"
        with self.lock:
            self.buffer.append(line)

    def _write_file(self, text: str):
        if not self.enabled:
            return
        try:
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(text + "\n")
        except Exception:
            # Avoid crashing on log write failures.
            pass

    def info(self, msg: str):
        self._append_ui(msg)
        if self.enabled:
            self._write_file(f"[{self._timestamp()}] {msg}")

    def error(self, msg: str):
        hint = f"{msg} (details: {self.log_path})" if self.enabled else msg
        self._append_ui(f"ERROR: {hint}")
        self._write_file(f"[{self._timestamp()}] ERROR: {msg}")

    def exception(self, prefix: str):
        tb = traceback.format_exc().strip()
        self.error(f"{prefix}; see {self.log_path}")
        if tb:
            self._write_file(f"[{self._timestamp()}] {prefix}\n{tb}\n")

    def snapshot(self, limit: int) -> list[str]:
        with self.lock:
            return list(self.buffer[-limit:])
