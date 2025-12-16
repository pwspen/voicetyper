from __future__ import annotations

import threading
from typing import Iterable

import numpy as np
import torch

from voicetyper.logging_utils import DebugSink


class SileroVoiceActivityDetector:
    """
    Thin wrapper around Silero VAD for streaming frames.
    """

    def __init__(self, sample_rate: int, sink: DebugSink | None = None):
        self.sample_rate = sample_rate
        if self.sample_rate not in (8000, 16000):
            raise ValueError("Silero VAD supports only 8000 or 16000 Hz sample rate.")
        self._lock = threading.Lock()
        self._model, self._get_speech_timestamps = self._load_model()
        self._state = None
        self._sink = sink
        self._required_samples = 512 if self.sample_rate == 16000 else 256

    def _load_model(self):
        model, utils = torch.hub.load(
            repo_or_dir="snakers4/silero-vad",
            model="silero_vad",
            force_reload=False,
            trust_repo=True,
        )
        (get_speech_timestamps, _, _, _, _) = utils
        return model, get_speech_timestamps

    def is_speech(self, audio_chunk: bytes) -> bool:
        """
        audio_chunk: PCM16LE mono bytes, any length. Will be resliced to required frame size.
        """
        if not audio_chunk:
            return False

        samples = np.frombuffer(audio_chunk, dtype=np.int16)
        if len(samples) < self._required_samples:
            # Not enough to evaluate; treat as silence
            return False
        # Use the last required_samples to keep latency low
        window = samples[-self._required_samples :]
        audio = torch.from_numpy(window.astype(np.float32) / 32768.0)
        try:
            with self._lock:
                speech_prob = self._model(audio, self.sample_rate).item()
            return speech_prob > 0.5
        except Exception:
            if self._sink:
                self._sink.exception("vad error")
            return False

    def collect_speech_windows(
        self,
        frames: Iterable[bytes],
        silence_timeout: float,
        frame_duration: float,
    ):
        """
        Generator yielding tuples (is_speech, frame_bytes).
        Tracks silence to signal stop.
        """
        silence_frames = 0
        silence_threshold = int(silence_timeout / frame_duration)
        for frame in frames:
            speech = self.is_speech(frame)
            if speech:
                silence_frames = 0
            else:
                silence_frames += 1
            yield speech, frame
            if silence_frames >= silence_threshold:
                break
