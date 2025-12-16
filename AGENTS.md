This project is a speech to text client for Linux x11. Currently it mainly uses the Speechmatics API.

The project focus is on low latency, and outputting the text via xdotool to let the user type into any window with their voice.

There is also a focus on ease of use and extensibility, so it has things like a screen where you can see different mic inputs to make it easy to tell which is which, as well as a thorough debug system to allow the user some visibility into what is going on inside the client. 

Coder Agent: Your focus should be on conciseness, extensability, and readability. Do not duplicate things if you aren't forced to, and your code should be easily maintainable by all future agents, and it should be very clear what each part does.

Speechmatics Realtime API quirks and constraints (important for this client):
- Concurrency caps are strict (Free tier: 2 sessions, Paid: 50); server returns `Concurrent Quota Exceeded` via an Error message and then closes. Hitting the cap can happen if old sessions linger after stop, so keep session counts visible and avoid overlapping starts.
- Sessions end server-side on 48h duration, 1h of no audio, or 3 minutes without audio or ping/pongs. Clients can end early; new sessions typically start in <1s. Recommended: if the server emits errors `quota_exceeded`/`job_error`/`internal_error` immediately after connect, back off 5–10 seconds before retry.
- Message flow: StartRecognition -> AddAudio chunks (binary) with AudioAdded acks -> AddPartialTranscript/AddTranscript outputs -> EndOfStream -> EndOfTranscript. Partials are low-latency but may revise; finals are stable but slower.
- Handshake is a WebSocket GET with Bearer token (or browser JWT param). The SDK raises `TranscriptionError` on server Error messages; surface these cleanly in logs.
- The server can emit `Info` messages immediately after handshake; type `concurrent_session_usage` includes current usage/quota, and type `recognition_quality` reports model selection. Warnings cover duration/idle limits and protocol issues like `add_audio_after_eos`. Error types map to close codes (e.g., `quota_exceeded` => WS code 4005) and terminate the session immediately.
