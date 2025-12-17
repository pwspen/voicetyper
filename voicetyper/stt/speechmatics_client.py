from __future__ import annotations

import asyncio
import threading
import time
from typing import Callable

from httpx import HTTPStatusError
import speechmatics
from speechmatics import models, client

from voicetyper.config import AppConfig
from voicetyper.stt.base import ErrorHandler, FinalHandler, PartialHandler, TranscriptionBackend
from voicetyper.audio.capture import QueueAudioStream

_active_sessions = 0
_sessions_lock = threading.Lock()


def _change_sessions(delta: int) -> int:
    global _active_sessions
    with _sessions_lock:
        _active_sessions += delta
        return _active_sessions


class SpeechmaticsBackend(TranscriptionBackend):
    def __init__(self, config: AppConfig, log_fn: Callable[[str], None] | None = None):
        self.config = config
        self._ws: client.WebsocketClient | None = None
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event = threading.Event()
        self._log = log_fn or (lambda _msg: None)
        self._audio_stream: QueueAudioStream | None = None
        self._running = False

    # Legacy interface compliance; not used in the long-lived flow.
    def stream(self, *args, **kwargs):
        raise NotImplementedError("SpeechmaticsBackend.stream is replaced by start_session/send_audio.")

    def start_session(
        self,
        on_partial: PartialHandler,
        on_final: FinalHandler,
        on_error: ErrorHandler,
    ) -> None:
        """
        Start a single long-lived Speechmatics session. Audio is pushed via send_audio().
        """
        if self._running:
            return
        self._stop_event.clear()
        self._audio_stream = QueueAudioStream()

        def run_loop():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            session_start = time.time()
            try:
                self._loop.run_until_complete(
                    self._run_ws(on_partial, on_final, on_error, session_start)
                )
            finally:
                try:
                    self._loop.run_until_complete(self._loop.shutdown_asyncgens())
                finally:
                    self._loop.close()
                    self._loop = None

        self._thread = threading.Thread(target=run_loop, daemon=True)
        self._thread.start()

    async def _run_ws(
        self,
        on_partial: PartialHandler,
        on_final: FinalHandler,
        on_error: ErrorHandler,
        session_start: float,
    ):
        self._running = True
        try:
            conn_settings = models.ConnectionSettings(
                url=self.config.connection_url,
                auth_token=self.config.resolve_api_key(),
            )
            audio_settings = models.AudioSettings(
                sample_rate=self.config.sample_rate,
                encoding="pcm_s16le",
            )
            transcription_config = models.TranscriptionConfig(
                operating_point="enhanced",
                language=self.config.language,
                enable_partials=True,
                max_delay=self.config.max_delay,
            )
            ws = client.WebsocketClient(conn_settings)
            self._ws = ws

            ws.add_event_handler(
                event_name=models.ServerMessageType.AddPartialTranscript,
                event_handler=lambda msg: on_partial(msg["metadata"]["transcript"]),
            )
            ws.add_event_handler(
                event_name=models.ServerMessageType.AddTranscript,
                event_handler=lambda msg: on_final(msg["metadata"]["transcript"]),
            )

            active = _change_sessions(+1)
            self._log(f"speechmatics: session start (active={active})")
            await ws.run(self._audio_stream, transcription_config, audio_settings)
        except HTTPStatusError as exc:
            on_error(exc)
        except Exception as exc:  # pragma: no cover - protect against unexpected issues
            on_error(exc)
        finally:
            active = _change_sessions(-1)
            duration = time.time() - session_start
            self._log(f"speechmatics: session end (active={active}, duration={duration:.2f}s)")
            self._running = False

    def send_audio(self, data: bytes) -> None:
        if not self._running or not self._audio_stream:
            return
        self._audio_stream.push(data)

    def end_utterance(self) -> None:
        """
        Force the server to finalize the current utterance without closing the session.
        """
        if not (self._loop and self._ws and self._running):
            return
        try:
            asyncio.run_coroutine_threadsafe(
                self._ws.send_message("ForceEndOfUtterance"), self._loop
            )
            self._log("speechmatics: ForceEndOfUtterance sent")
        except Exception as exc:  # pragma: no cover
            self._log(f"speechmatics: ForceEndOfUtterance failed: {exc}")

    def stop(self) -> None:
        self._stop_event.set()
        if self._audio_stream:
            self._audio_stream.close()
        if self._ws and self._loop and self._running:
            try:
                self._log("speechmatics: stop requested")
                asyncio.run_coroutine_threadsafe(self._ws.stop(), self._loop)
            except Exception:
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
            self._thread = None
        self._ws = None
        self._running = False
