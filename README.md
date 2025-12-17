## VoiceTyper

Terminal-based voice typing using Speechmatics Realtime API, Silero VAD, and xdotool for low-latency text entry.

### Setup
1. Install deps with `uv sync`.
2. Export your key: `export SPEECHMATICS_API_KEY=...`.
3. Ensure `xdotool` is installed on your system.
4. Default audio sample rate is 16 kHz (required for Silero); capture chunk size defaults to 50 ms (Speechmatics). Silero VAD internally reslices to 512 samples (32 ms) at 16 kHz to satisfy model requirements.
5. Enable debug in `voicetyper/config.py` to log all events to `voicetyper-debug.log` and show recent events in the UI.

### Run
- Preferred: `uv run python -m voicetyper` (uses package module path)
- Or after `uv sync`, the console script `voicetyper` is available in `.venv/bin`

### Features
- Mic selection screen with live level indicator.
- Speechmatics connection idles out after configurable VAD silence (default 10s via `ws_idle_timeout`) to avoid concurrency caps while keeping latency low.
- Speechmatics partial vs final handling:
  - Speechmatics emits fast, revisable partials and slower, stable finals. By default, the client types finals only; set `prefer_partials=True` in `voicetyper/config.py` to type partials live.
  - The client listens for keywords in both partials and finals. Saying the end-utterance keyword (default: `stop`) drops any pending partial text and sends a `ForceEndOfUtterance` to Speechmatics. Saying the enter keyword (default: `enter`) sends an Enter keypress (without typing the word). If a final contains a keyword, everything from the keyword onward is discarded; only the text before it is typed.


### Dev Log
- Extending silence_timeout *mostly* fixed an issue where we had the following happen: The ends of utterances would "stick around" and only be reported as final upon the start of the next utterance. This caused issues like "enter" only being pressed on the start of the next line - which ends up ok but the user doesn't get quick feedback. This can be held in the buffer for an indefinite period of time due to the way Speechmatics API works.
