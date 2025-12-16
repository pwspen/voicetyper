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