from __future__ import annotations

import queue
import threading
from typing import Callable, Iterable

import numpy as np
import sounddevice as sd


class AudioLevelMeter:
    def __init__(self):
        self._lock = threading.Lock()
        self._rms = 0.0

    def update(self, data: bytes):
        # Convert to numpy for RMS calculation
        if not data:
            return
        audio = np.frombuffer(data, dtype=np.int16)
        if audio.size == 0:
            return
        with np.errstate(all="ignore"):
            rms_val = np.sqrt(np.mean(np.square(audio)))
        if not np.isfinite(rms_val):
            return
        rms = float(rms_val) / 32768.0
        with self._lock:
            self._rms = rms

    def level(self) -> float:
        with self._lock:
            return self._rms


class QueueAudioStream:
    """
    File-like wrapper that Speechmatics client can read from.
    """

    def __init__(self):
        self._queue: queue.Queue[bytes | None] = queue.Queue()
        self.closed = False

    def push(self, data: bytes):
        if self.closed:
            return
        self._queue.put(data)

    def close(self):
        self.closed = True
        self._queue.put(None)

    def read(self, _size: int | None = None) -> bytes:
        chunk = self._queue.get()
        if chunk is None:
            return b""
        return chunk


class MicrophoneStream:
    def __init__(
        self,
        device_index: int,
        sample_rate: int,
        chunk_ms: int,
        channels: int = 1,
        level_meter: AudioLevelMeter | None = None,
    ):
        self.device_index = device_index
        self.sample_rate = sample_rate
        self.chunk_ms = chunk_ms
        self.channels = channels
        self.level_meter = level_meter

        self._stream: sd.RawInputStream | None = None
        self._queue: queue.Queue[bytes] = queue.Queue()
        self._stop_event = threading.Event()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()

    def start(self):
        if self._stream:
            return

        frame_count = int(self.sample_rate * self.chunk_ms / 1000)

        def callback(indata, frames, _time, status):
            if status:
                # Non-fatal; drop if needed
                pass
            data_bytes = bytes(indata)
            if self.level_meter:
                self.level_meter.update(data_bytes)
            if not self._stop_event.is_set():
                self._queue.put(data_bytes)

        self._stream = sd.RawInputStream(
            samplerate=self.sample_rate,
            blocksize=frame_count,
            device=self.device_index,
            dtype="int16",
            channels=self.channels,
            callback=callback,
        )
        self._stream.start()

    def stop(self):
        self._stop_event.set()
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        # Drain queue
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

    def frames(self) -> Iterable[bytes]:
        """
        Yield audio frames as bytes until stopped.
        """
        while not self._stop_event.is_set():
            try:
                frame = self._queue.get(timeout=0.5)
                yield frame
            except queue.Empty:
                continue

    def attach_to(self, audio_stream: QueueAudioStream):
        """
        Push frames into a QueueAudioStream until stopped.
        """
        for frame in self.frames():
            audio_stream.push(frame)
        audio_stream.close()
