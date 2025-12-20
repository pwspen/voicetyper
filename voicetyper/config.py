from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class KeywordAction:
    word: str
    keys: list[str] = field(default_factory=list)
    force_end: bool = True


def default_keyword_actions() -> list[KeywordAction]:
    return [
        KeywordAction(word="enter", keys=["KP_Enter"], force_end=True),
    ]


def parse_keyword_actions(raw: object) -> list[KeywordAction]:
    """
    Convert user-provided keyword config into KeywordAction instances.

    Supports legacy dict format with "end_utterance"/"enter" strings and
    new formats:
      - list of {"word": "...", "keys": [...], "force_end": bool}
      - dict mapping word -> {"keys": [...], "force_end": bool}
    """
    actions: list[KeywordAction] = []

    if isinstance(raw, dict):
        # Legacy format: keep enter; ignore deprecated end_utterance keyword
        if "end_utterance" in raw or "enter" in raw:
            enter_word = raw.get("enter")
            if enter_word:
                actions.append(KeywordAction(word=str(enter_word), keys=["KP_Enter"], force_end=True))
            return actions

        # Mapping of word -> options
        candidates: list[dict] = []
        for word, cfg in raw.items():
            if isinstance(cfg, dict):
                item = cfg.copy()
                item["word"] = word
            else:
                item = {"word": word}
            candidates.append(item)
    elif isinstance(raw, list):
        candidates = [item for item in raw if isinstance(item, dict)]
    else:
        return actions

    for item in candidates:
        word = str(item.get("word") or "").strip()
        if not word:
            continue
        keys_raw = item.get("keys", [])
        if isinstance(keys_raw, str):
            keys = [keys_raw]
        elif isinstance(keys_raw, list):
            keys = [str(k) for k in keys_raw if str(k).strip()]
        else:
            keys = []
        force_end_raw = item.get("force_end")
        force_end = True if force_end_raw is None else bool(force_end_raw)
        actions.append(KeywordAction(word=word, keys=keys, force_end=force_end))

    # Stop keyword is deprecated/disabled.
    actions = [a for a in actions if a.word.strip().lower() != "stop"]
    return actions


@dataclass
class AppConfig:
    api_key: str | None = None
    api_key_validated: bool = False
    connection_url: str = "wss://eu.rt.speechmatics.com/v2"
    language: str = "en"
    sample_rate: int = 16000
    silence_timeout: float = 2.0
    prefer_partials: bool = False
    listen_hotkey: str = "g"
    debug: bool = True
    debug_log_path: str = "voicetyper-debug.log"
    min_stream_seconds: float = 1.0
    keyword_actions: list[KeywordAction] = field(default_factory=default_keyword_actions)
    max_delay: float = 2.0
    ws_idle_timeout: float = 4.0
    notifications_enabled: bool = False
    hotkey_toggle_listening: str | None = None

    def resolve_api_key(self) -> str:
        api_key = self.api_key or os.environ.get("SPEECHMATICS_API_KEY") or ""
        return api_key

    def has_valid_api_key(self) -> bool:
        """True if an env var is present or a stored key has been validated."""
        if os.environ.get("SPEECHMATICS_API_KEY"):
            return True
        return bool(self.api_key and self.api_key_validated)


def get_config_path() -> Path:
    """Get configuration file path respecting XDG_CONFIG_HOME."""
    xdg_config = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config:
        base = Path(xdg_config)
    else:
        base = Path.home() / ".config"
    return base / "voicetyper" / "config.json"


def create_default_config_file(path: Path) -> None:
    """Create default configuration file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    default_config = {
        "notifications": {
            "enabled": False
        },
        "hotkey": {
            "toggle_listening": "<Control><Alt>v"
        },
        "api": {
            "key": None,
            "validated": False,
            "connection_url": "wss://eu.rt.speechmatics.com/v2"
        },
        "audio": {
            "sample_rate": 16000,
            "silence_timeout": 2.0,
            "min_stream_seconds": 1.0
        },
        "transcription": {
            "language": "en",
            "prefer_partials": False,
            "max_delay": 2.0,
            "ws_idle_timeout": 4.0
        },
        "keywords": [
            {
                "word": "enter",
                "keys": ["KP_Enter"],
                "force_end": True
            }
        ],
        "debug": {
            "enabled": True,
            "log_path": "voicetyper-debug.log"
        },
        "terminal": {
            "listen_hotkey": "g"
        }
    }
    try:
        with open(path, 'w') as f:
            json.dump(default_config, f, indent=2)
    except Exception as e:
        print(f"Warning: Could not create config file: {e}", file=sys.stderr)


def load_config() -> AppConfig:
    """
    Load configuration with layered priority:
    1. Built-in defaults
    2. Config file values
    3. Environment variables
    """
    config_path = get_config_path()

    # Create default file if missing
    if not config_path.exists():
        create_default_config_file(config_path)

    # Start with defaults
    config = AppConfig()

    # Load and merge file config
    if config_path.exists():
        try:
            with open(config_path, 'r') as f:
                data = json.load(f)

                # Notifications
                if "notifications" in data:
                    config.notifications_enabled = data["notifications"].get("enabled", False)

                # Hotkey
                if "hotkey" in data:
                    config.hotkey_toggle_listening = data["hotkey"].get("toggle_listening")

                # API settings
                if "api" in data:
                    api = data["api"]
                    if api.get("key"):
                        config.api_key = api["key"]
                        if "validated" not in api:
                            config.api_key_validated = True
                    if "validated" in api:
                        config.api_key_validated = bool(api["validated"])
                    if api.get("connection_url"):
                        config.connection_url = api["connection_url"]

                # Audio settings
                if "audio" in data:
                    audio = data["audio"]
                    if "sample_rate" in audio:
                        config.sample_rate = audio["sample_rate"]
                    if "silence_timeout" in audio:
                        config.silence_timeout = audio["silence_timeout"]
                    if "min_stream_seconds" in audio:
                        config.min_stream_seconds = audio["min_stream_seconds"]

                # Transcription settings
                if "transcription" in data:
                    trans = data["transcription"]
                    if "language" in trans:
                        config.language = trans["language"]
                    if "prefer_partials" in trans:
                        config.prefer_partials = trans["prefer_partials"]
                    if "max_delay" in trans:
                        config.max_delay = trans["max_delay"]
                    if "ws_idle_timeout" in trans:
                        config.ws_idle_timeout = trans["ws_idle_timeout"]

                # Keywords
                if "keywords" in data:
                    parsed_keywords = parse_keyword_actions(data["keywords"])
                    if parsed_keywords:
                        config.keyword_actions = parsed_keywords

                # Debug
                if "debug" in data:
                    debug = data["debug"]
                    if "enabled" in debug:
                        config.debug = debug["enabled"]
                    if "log_path" in debug:
                        config.debug_log_path = debug["log_path"]

                # Terminal
                if "terminal" in data:
                    terminal = data["terminal"]
                    if "listen_hotkey" in terminal:
                        config.listen_hotkey = terminal["listen_hotkey"]

        except Exception as e:
            print(f"Warning: Could not load config file: {e}", file=sys.stderr)

    return config


def save_config(config: AppConfig, path: Path | None = None) -> bool:
    """
    Save configuration to JSON file.

    Args:
        config: AppConfig instance to save
        path: Optional path override (defaults to standard config path)

    Returns:
        True if save succeeded, False otherwise
    """
    if path is None:
        path = get_config_path()

    # Convert AppConfig to nested dict matching JSON structure
    config_dict = {
        "notifications": {
            "enabled": config.notifications_enabled
        },
        "hotkey": {
            "toggle_listening": config.hotkey_toggle_listening
        },
        "api": {
            "key": config.api_key,
            "validated": config.api_key_validated,
            "connection_url": config.connection_url
        },
        "audio": {
            "sample_rate": config.sample_rate,
            "silence_timeout": config.silence_timeout,
            "min_stream_seconds": config.min_stream_seconds
        },
        "transcription": {
            "language": config.language,
            "prefer_partials": config.prefer_partials,
            "max_delay": config.max_delay,
            "ws_idle_timeout": config.ws_idle_timeout
        },
        "keywords": [
            {
                "word": action.word,
                "keys": action.keys,
                "force_end": action.force_end
            }
            for action in config.keyword_actions
        ],
        "debug": {
            "enabled": config.debug,
            "log_path": config.debug_log_path
        },
        "terminal": {
            "listen_hotkey": config.listen_hotkey
        }
    }

    try:
        path.parent.mkdir(parents=True, exist_ok=True)

        # Atomic write: write to temp file then rename
        temp_path = path.with_suffix('.tmp')
        with open(temp_path, 'w') as f:
            json.dump(config_dict, f, indent=2)
        temp_path.replace(path)

        return True
    except (OSError, IOError, PermissionError) as e:
        print(f"Error saving config: {e}", file=sys.stderr)
        return False


DEFAULT_CONFIG = load_config()
