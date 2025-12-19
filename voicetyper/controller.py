from __future__ import annotations

import re
import threading
import time
from typing import Callable

from voicetyper import output
from voicetyper.audio.capture import AudioLevelMeter, MicrophoneStream
from voicetyper.audio.devices import InputDevice
from voicetyper.audio.vad import SileroVoiceActivityDetector
from voicetyper.config import AppConfig, KeywordAction
from voicetyper.logging_utils import DebugSink
from voicetyper.stt.base import TranscriptionBackend


class TranscriptRouter:
    def __init__(
        self,
        prefer_partials: bool,
        keyword_actions: list[KeywordAction],
        request_force_end: Callable[[str], None],
        send_keys: Callable[[list[str]], None],
        log_fn: Callable[[str], None] | None = None,
    ):
        self.prefer_partials = prefer_partials
        self.keyword_actions = [
            KeywordAction(
                word=action.word.strip().lower(),
                keys=[k for k in action.keys if str(k).strip()],
                force_end=action.force_end,
            )
            for action in keyword_actions
            if action.word.strip()
        ]
        self.request_force_end = request_force_end
        self.send_keys = send_keys
        self.log = log_fn or (lambda _msg: None)
        self._suppress_output = False
        self._content_seen: bool = False
        self._committed: str = ""
        self._keyword_triggered: bool = False

    def start_utterance(self):
        self._suppress_output = False
        self._content_seen = False
        self._committed = ""
        self._keyword_triggered = False

    def _strip_keywords(self, text: str) -> str:
        cleaned = text
        for action in self.keyword_actions:
            pattern = rf"\b{re.escape(action.word)}\b[^\w\s]*"
            cleaned = re.sub(pattern, " ", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned if cleaned.strip() else ""

    def _first_keyword_pos(self, text: str) -> tuple[KeywordAction | None, int | None]:
        first: tuple[KeywordAction | None, int | None] = (None, None)
        for action in self.keyword_actions:
            match = re.search(rf"\b{re.escape(action.word)}\b[^\w\s]*", text, flags=re.IGNORECASE)
            if match:
                if first[1] is None or match.start() < first[1]:
                    first = (action, match.start())
        return first

    def _has_content(self, text: str) -> bool:
        return bool(re.search(r"\w", text))

    def _overlap_len(self, a: str, b: str) -> int:
        max_len = min(len(a), len(b))
        for size in range(max_len, 0, -1):
            if a[-size:] == b[:size]:
                return size
        return 0

    def _append_text(self, text: str, log_label: str):
        cleaned = self._strip_keywords(text)
        if not cleaned:
            return
        overlap = self._overlap_len(self._committed, cleaned)
        if overlap == len(cleaned):
            self.log(f"{log_label} skipped: contained in committed")
            return
        delta = cleaned[overlap:]
        if not delta:
            return
        self.log(f"{log_label}: {delta}")
        output.xdotool.send_text(delta)
        self._committed += delta

    def on_partial(self, text: str):
        self.log(f"partial: {text}")
        if self._has_content(text):
            self._content_seen = True
        if self._suppress_output or self._keyword_triggered:
            return
        if self.prefer_partials:
            self._append_text(text, "type_partial")

    def on_final(self, text: str):
        self.log(f"final: {text}")
        has_content = self._has_content(text)
        if not self._content_seen and not has_content:
            self.log("final skipped: before first content of utterance")
            return
        if has_content:
            self._content_seen = True
        if self._suppress_output or self._keyword_triggered:
            return
        action, pos = self._first_keyword_pos(text)
        if action and pos is not None:
            before = text[:pos]
            self._append_text(before, "type_final")
            self._keyword_triggered = True
            self._suppress_output = True
            if action.keys:
                self.log(f"keyword: {action.word} send keys {action.keys}")
                self.send_keys(action.keys)
            else:
                self.log(f"keyword: {action.word} (no key bindings)")
            if action.force_end:
                self.request_force_end(action.word)
            return
        self._append_text(text, "type_final")

    def flush_partial_as_final(self):
        # Auto-finalize removed; finals drive commits.
        return


class VoiceController:
    def __init__(
        self,
        config: AppConfig,
        backend_factory: Callable[[], TranscriptionBackend],
        device: InputDevice,
        sink: DebugSink,
    ):
        self.config = config
        self.backend_factory = backend_factory
        self.device = device
        self.sink = sink
        self._log = sink.info
        self.vad = SileroVoiceActivityDetector(sample_rate=config.sample_rate, sink=self.sink)
        self.enabled = False
        self.listening = False
        self._worker: threading.Thread | None = None
        self._session_lock = threading.Lock()
        self._backend: TranscriptionBackend | None = None
        self._force_end_event = threading.Event()

    def _request_force_end(self, keyword: str):
        if self._force_end_event.is_set():
            return
        self._force_end_event.set()
        self._log(f"utterance: force end (keyword={keyword})")
        if self._backend:
            self._backend.end_utterance()

    def _send_keys(self, keys: list[str]):
        for key in keys:
            output.xdotool.send_key(key)

    def _listener_loop(self):
        """
        Long-lived capture loop: waits for speech, streams the utterance, repeats while enabled.
        """
        try:
            if not self._session_lock.acquire(blocking=False):
                self._log("listener busy, skipping start")
                return
            router = TranscriptRouter(
                prefer_partials=self.config.prefer_partials,
                keyword_actions=self.config.keyword_actions,
                request_force_end=self._request_force_end,
                send_keys=self._send_keys,
                log_fn=self._log if self.config.debug else None,
            )
            backend_errors: list[Exception] = []
            backend = self.backend_factory()
            self._backend = backend

            def on_error(exc: Exception):
                backend_errors.append(exc)
                self.sink.exception(f"speechmatics error: {exc}")

            def backend_running() -> bool:
                if not backend:
                    return False
                is_running = getattr(backend, "is_running", None)
                return bool(is_running()) if is_running else False

            def ensure_backend():
                if not backend_running():
                    backend.start_session(router.on_partial, router.on_final, on_error)

            ensure_backend()
            level_meter = AudioLevelMeter()
            mic = MicrophoneStream(
                device_index=self.device.index,
                sample_rate=self.config.sample_rate,
                channels=1,
                level_meter=level_meter,
            )
            mic.start()
            last_vad_speech = time.time()
            idle_timeout = self.config.ws_idle_timeout
            try:
                while self.enabled:
                    # Wait for speech to start
                    for frame in mic.frames():
                        if not self.enabled:
                            backend.stop()
                            return
                        if backend_errors:
                            return
                        now = time.time()
                        if (
                            idle_timeout > 0
                            and backend_running()
                            and not self.listening
                            and (now - last_vad_speech) >= idle_timeout
                        ):
                            self._log("speechmatics: idle timeout; closing session")
                            backend.stop()
                            continue
                        if self.vad.is_speech(frame):
                            last_vad_speech = now
                            self._force_end_event.clear()
                            router.start_utterance()
                            self._log("vad: speech detected")
                            ensure_backend()
                            self.listening = True
                            last_speech = now
                            start_time = last_speech
                            session_start = time.time()
                            backend.send_audio(frame)

                            # Continue until silence timeout
                            for frame2 in mic.frames():
                                if not self.enabled:
                                    self._log("utterance: stop (disabled mid-stream)")
                                    backend.end_utterance()
                                    backend.stop()
                                    self.listening = False
                                    return
                                if backend_errors:
                                    backend.stop()
                                    self.listening = False
                                    return
                                if self._force_end_event.is_set():
                                    self._log("utterance: stop (keyword)")
                                    break
                                backend.send_audio(frame2)
                                speech = self.vad.is_speech(frame2)
                                if speech:
                                    last_speech = time.time()
                                    last_vad_speech = last_speech
                                silence = time.time() - last_speech
                                elapsed = time.time() - start_time
                                silence_limit = self.config.silence_timeout
                                if elapsed >= self.config.min_stream_seconds and silence >= silence_limit:
                                    router.flush_partial_as_final()
                                    break
                            backend.end_utterance()
                            self.listening = False
                            duration = time.time() - session_start
                            self._log(f"utterance: stop (duration={duration:.2f}s)")
                            break  # go back to waiting for next speech
                    time.sleep(0.01)
            finally:
                mic.stop()
        except Exception as exc:
            self.sink.exception(f"listener loop error: {exc}")
            raise
        finally:
            if self._backend:
                self._backend.stop()
                self._backend = None
            if self._session_lock.locked():
                self._session_lock.release()

    def set_enabled(self, enabled: bool):
        if enabled:
            if self.enabled:
                return
            self.enabled = True
            self._log("listening enabled")
            thread = threading.Thread(target=self._listener_loop, daemon=True)
            thread.start()
            self._worker = thread
        else:
            if not self.enabled:
                return
            self.enabled = False
            self.listening = False
            self._log("listening disabled")
            if self._backend:
                self._backend.stop()
                self._backend = None

    def toggle_enabled(self):
        self.set_enabled(not self.enabled)
