from __future__ import annotations

import re
import threading
import time
from typing import Callable

from voicetyper import output
from voicetyper.audio.capture import AudioLevelMeter, MicrophoneStream
from voicetyper.audio.devices import InputDevice
from voicetyper.audio.vad import SileroVoiceActivityDetector
from voicetyper.config import AppConfig
from voicetyper.logging_utils import DebugSink
from voicetyper.stt.base import TranscriptionBackend


class TranscriptRouter:
    def __init__(
        self,
        prefer_partials: bool,
        end_keyword: str,
        enter_keyword: str,
        keyword_final_grace: float,
        request_force_end: Callable[[str], None],
        send_enter: Callable[[], None],
        log_fn: Callable[[str], None] | None = None,
    ):
        self.prefer_partials = prefer_partials
        self.end_keyword = end_keyword.strip().lower()
        self.enter_keyword = enter_keyword.strip().lower()
        self.keyword_final_grace = keyword_final_grace
        self.request_force_end = request_force_end
        self.send_enter = send_enter
        self.log = log_fn or (lambda _msg: None)
        self._suppress_output = False
        self._keyword_counts: dict[str, int] = {}
        self._last_sent_partial = ""
        self._last_partial_raw = ""
        self._last_forced_final = ""
        self._keyword_seen = False
        self._force_end_sent = False
        self._pending_force_end = False
        self._pending_force_end_generation: int | None = None
        self._generation = 0
        self._pending_keyword: str | None = None
        self._pending_pre_text: str = ""
        self._pending_keyword_generation: int | None = None
        self._keyword_timer: threading.Timer | None = None
        self._last_force_end_time: float | None = None
        self._first_partial_seen: bool = False

    def start_utterance(self):
        self._generation += 1
        self._suppress_output = False
        self._keyword_counts = {
            kw: 0 for kw in (self.end_keyword, self.enter_keyword) if kw
        }
        self._last_sent_partial = ""
        self._last_partial_raw = ""
        self._last_forced_final = ""
        self._keyword_seen = False
        self._force_end_sent = False
        self._pending_force_end = False
        self._pending_force_end_generation = None
        self._pending_keyword = None
        self._pending_pre_text = ""
        self._pending_keyword_generation = None
        if self._keyword_timer:
            self._keyword_timer.cancel()
        self._keyword_timer = None
        self._last_force_end_time = None
        self._first_partial_seen = False

    def _tokenize(self, text: str) -> list[str]:
        return re.findall(r"\b\w+\b", text.lower())

    def _handle_keywords(self, text: str):
        for keyword in (self.end_keyword, self.enter_keyword):
            if not keyword:
                continue
            pattern = rf"\b{re.escape(keyword)}\b[^\w\s]*"
            count = len(re.findall(pattern, text, flags=re.IGNORECASE))
            prev = self._keyword_counts.get(keyword, 0)
            if count > prev and self._pending_keyword is None:
                self._keyword_counts[keyword] = count
                self._keyword_seen = True
                self._pending_keyword = keyword
                self._pending_pre_text = self._text_before_first_keyword(text)
                self._pending_keyword_generation = self._generation
                self.log(
                    f"keyword pending: {keyword} (waiting {self.keyword_final_grace:.1f}s for final)"
                )
                if self._keyword_timer:
                    self._keyword_timer.cancel()
                self._keyword_timer = threading.Timer(
                    self.keyword_final_grace, self._keyword_timeout_fire, args=(self._generation,)
                )
                self._keyword_timer.start()
                # Stop processing additional keywords once one is pending.
                break

    def _strip_keywords(self, text: str) -> str:
        cleaned = text
        for keyword in (self.end_keyword, self.enter_keyword):
            if not keyword:
                continue
            pattern = rf"\b{re.escape(keyword)}\b[^\w\s]*"
            cleaned = re.sub(pattern, " ", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned if cleaned.strip() else ""

    def _text_before_first_keyword(self, text: str) -> str:
        positions: list[tuple[int, int]] = []
        for keyword in (self.end_keyword, self.enter_keyword):
            if not keyword:
                continue
            match = re.search(rf"\b{re.escape(keyword)}\b[^\w\s]*", text, flags=re.IGNORECASE)
            if match:
                positions.append((match.start(), match.end()))
        if not positions:
            return text
        start, _ = min(positions, key=lambda p: p[0])
        return text[:start]

    def _keyword_timeout_fire(self, generation: int):
        if generation != self._generation:
            return
        if not self._pending_keyword:
            return
        keyword = self._pending_keyword
        pre = self._pending_pre_text
        cleaned = self._strip_keywords(pre)
        if cleaned and cleaned != self._last_forced_final:
            self.log(f"type_keyword_fallback: {cleaned}")
            output.xdotool.send_text(cleaned)
            self._last_forced_final = cleaned
        self.log(f"keyword timeout: forcing end ({keyword})")
        self._pending_keyword = None
        self._suppress_output = True
        self.request_force_end(keyword)
        self._last_force_end_time = time.time()
        self._force_end_sent = True
        self._pending_force_end = True
        self._pending_force_end_generation = self._generation
        if keyword == self.enter_keyword:
            self.log("keyword: enter (timeout)")
            self.send_enter()

    def _text_before_first_keyword(self, text: str) -> str:
        positions: list[tuple[int, int]] = []
        for keyword in (self.end_keyword, self.enter_keyword):
            if not keyword:
                continue
            match = re.search(rf"\b{re.escape(keyword)}\b[^\w\s]*", text, flags=re.IGNORECASE)
            if match:
                positions.append((match.start(), match.end()))
        if not positions:
            return text
        start, _ = min(positions, key=lambda p: p[0])
        return text[:start]

    def _first_keyword_pos(self, text: str) -> int | None:
        positions: list[int] = []
        for keyword in (self.end_keyword, self.enter_keyword):
            if not keyword:
                continue
            match = re.search(rf"\b{re.escape(keyword)}\b[^\w\s]*", text, flags=re.IGNORECASE)
            if match:
                positions.append(match.start())
        return min(positions) if positions else None

    def on_partial(self, text: str):
        self.log(f"partial: {text}")
        self._first_partial_seen = True
        self._last_partial_raw = text
        self._handle_keywords(text)
        if self._suppress_output or self._keyword_seen or self._pending_keyword:
            return
        if self.prefer_partials:
            cleaned = self._strip_keywords(text)
            if not cleaned:
                return
            if cleaned.startswith(self._last_sent_partial):
                delta = cleaned[len(self._last_sent_partial) :]
            else:
                delta = cleaned
            if delta:
                output.xdotool.send_text(delta)
                self._last_sent_partial = cleaned
                self._last_forced_final = cleaned
                self.log(f"type_partial: {delta}")

    def on_final(self, text: str):
        self.log(f"final: {text}")
        now = time.time()
        if self._last_force_end_time and (now - self._last_force_end_time) <= 1.0:
            self.log("final skipped: within post-force-end window")
            return
        if not self._first_partial_seen:
            self.log("final skipped: before first partial of utterance")
            return
        if self._pending_keyword and self._pending_keyword_generation != self._generation:
            self.log("final skipped: pending keyword belongs to different utterance")
            return
        if self._pending_force_end and self._pending_force_end_generation != self._generation:
            self.log("final skipped: arrived after new utterance started")
            return
        keyword_pos = self._first_keyword_pos(text)
        self._handle_keywords(text)
        if self._pending_keyword:
            # Consume the final during grace window.
            if self._keyword_timer:
                self._keyword_timer.cancel()
                self._keyword_timer = None
            pre = self._text_before_first_keyword(text)
            cleaned = self._strip_keywords(pre)
            if cleaned and cleaned != self._last_forced_final:
                self.log(f"type_final: {cleaned}")
                output.xdotool.send_text(cleaned)
                self._last_forced_final = cleaned
            keyword = self._pending_keyword
            self._pending_keyword = None
            self._suppress_output = True
            if keyword == self.enter_keyword:
                self.log("keyword: enter (final)")
                self.send_enter()
            elif keyword == self.end_keyword:
                self.log(f"keyword: end utterance ({self.end_keyword})")
            self._last_force_end_time = time.time()
            if self._pending_force_end:
                self._pending_force_end = False
                self._pending_force_end_generation = None
            return
        if keyword_pos is not None:
            text = text[:keyword_pos]
        elif self._suppress_output:
            return
        cleaned = self._strip_keywords(text)
        if cleaned and cleaned != self._last_forced_final:
            if self._last_forced_final and cleaned.startswith(self._last_forced_final):
                self.log(f"final skipped: prefix of typed content ({cleaned})")
            else:
                self.log(f"type_final: {cleaned}")
                output.xdotool.send_text(cleaned)
                self._last_forced_final = cleaned
        if self._pending_force_end:
            self._pending_force_end = False
            self._pending_force_end_generation = None

    def flush_partial_as_final(self):
        if self.prefer_partials:
            return
        if self._suppress_output:
            return
        if not self._last_partial_raw:
            return
        cleaned = self._strip_keywords(self._last_partial_raw)
        if cleaned and cleaned != self._last_forced_final:
            self.log("auto-finalize: partial->final")
            self.log(f"type_autofinal: {cleaned}")
            output.xdotool.send_text(cleaned)
            self._last_forced_final = cleaned


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

    def _send_enter_key(self):
        output.xdotool.send_key("KP_Enter")

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
                end_keyword=self.config.end_utterance_keyword,
                enter_keyword=self.config.enter_keyword,
                keyword_final_grace=self.config.keyword_final_grace_seconds,
                request_force_end=self._request_force_end,
                send_enter=self._send_enter_key,
                log_fn=self._log if self.config.debug else None,
            )
            backend_errors: list[Exception] = []
            backend = self.backend_factory()
            self._backend = backend

            def on_error(exc: Exception):
                backend_errors.append(exc)
                self.sink.exception(f"speechmatics error: {exc}")

            backend.start_session(router.on_partial, router.on_final, on_error)
            level_meter = AudioLevelMeter()
            mic = MicrophoneStream(
                device_index=self.device.index,
                sample_rate=self.config.sample_rate,
                chunk_ms=self.config.chunk_ms,
                channels=1,
                level_meter=level_meter,
            )
            mic.start()
            try:
                while self.enabled:
                    # Wait for speech to start
                    for frame in mic.frames():
                        if not self.enabled:
                            backend.stop()
                            return
                        if backend_errors:
                            return
                        if self.vad.is_speech(frame):
                            self._force_end_event.clear()
                            router.start_utterance()
                            self._log("vad: speech detected")
                            self.listening = True
                            last_speech = time.time()
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
                                silence = time.time() - last_speech
                                elapsed = time.time() - start_time
                                silence_limit = max(self.config.silence_timeout, self.config.auto_finalize_silence)
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
