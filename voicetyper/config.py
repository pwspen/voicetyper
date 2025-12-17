from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class AppConfig:
    api_key: str | None = None
    connection_url: str = "wss://eu.rt.speechmatics.com/v2"
    language: str = "en"
    sample_rate: int = 16000
    silence_timeout: float = 0.8
    prefer_partials: bool = False
    listen_hotkey: str = "g"
    debug: bool = True
    debug_log_path: str = "voicetyper-debug.log"
    min_stream_seconds: float = 1.0
    auto_finalize_silence: float = 1.2
    keyword_final_grace_seconds: float = 1.5
    end_utterance_keyword: str = "stop"
    enter_keyword: str = "enter"
    max_delay: float = 2.0

    def resolve_api_key(self) -> str:
        api_key = self.api_key or os.environ.get("SPEECHMATICS_API_KEY") or ""
        return api_key


DEFAULT_CONFIG = AppConfig()
