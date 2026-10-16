# Build a voice agent that books appointments

A mock clinic booking agent with a browser microphone, streaming LiveKit audio, request corrections, explicit spoken confirmation, and one booking per conversation.

**Implemented:** the browser connects to LiveKit over WebRTC, publishes microphone audio, subscribes to agent audio, and receives booking state and latency events. The booking service only accepts voice confirmation after the current offer completes uninterrupted SDK playout. A “yes” that starts before completion is rejected even if its transcript arrives later.

**Validated here:** 66 Python tests and 6 JavaScript tests. Two tests use the installed LiveKit AgentSession, tool scheduler, TTS audio frames, and audio-output buffer. These SDK tests use deterministic model and output-device fixtures. Browser results and live acceptance status are recorded in [validation results](docs/validation_results.json). Live cloud STT/LLM/TTS and physical microphone/earpiece behavior remain unvalidated.

[Watch the typed walkthrough](docs/typed_walkthrough.mp4): correction, exact time, date clarification, confirmation and retry. It is an automated browser recording with no microphone audio. Seven browser checks passed with no page errors.

[The tutorial](docs/tutorial.md) explains the code and how to run it. [The code review](docs/code_review.md) records the remaining production gaps. This is a local tutorial project; shared hosting needs authentication, session ownership checks and usage limits. Actual credentials belong in the ignored server `.env` or deployment secrets. Commit only the placeholder `.env.example`.

![Booking flow with corrections and retries](docs/booking_flow.png)

## Run browser voice

Use Python 3.11 or 3.12 and [uv](https://docs.astral.sh/uv/getting-started/installation/). Create a [LiveKit Cloud](https://cloud.livekit.io/) project, then:

```bash
uv sync --locked --extra voice
cp .env.example .env
```

Fill in `LIVEKIT_URL`, `LIVEKIT_API_KEY`, and `LIVEKIT_API_SECRET`. Leave `BOOKING_API_URL=http://127.0.0.1:8000` for the local setup. Keep the credentials in the server's `.env`; the browser receives only a short-lived room token.

Terminal 1:

```bash
uv run --extra voice server.py
```

Terminal 2:

```bash
uv run --extra voice voice_agent.py download-files
uv run --extra voice voice_agent.py dev
```

Open **http://127.0.0.1:8000**, click **Connect voice**, grant microphone permission, and speak. Use headphones. If browser autoplay is blocked, click **Enable audio**. **Mute** controls the published microphone; **Disconnect voice** closes the room connection.

The local token endpoint dispatches agent `clinic-booking` with the same booking session ID. Reconnecting preserves that patient's booking; **New session** explicitly creates a different fictional patient. Text submission is disabled for a voice session, so it cannot bypass the spoken-offer guard.

Try “Book me a dermatologist appointment Friday… actually, make that Monday.” Wait for the complete Monday offer, then say “yes.” The booking count should become one. The page displays the backend audit trail, agent state, offer completion, and server response/interruption timings. **Download voice trace** exports the actual received events as JSON.

The JavaScript LiveKit client is already bundled in `web/voice_bundle.js`. Node is only needed when modifying it:

```bash
npm ci
npm run build
```

## Keep the first local test simple

The text and terminal demos still need no LLM credentials:

```bash
python3 demo_journey.py
python3 server.py
```

The terminal journey uses a temporary database and a fixed date. It prints booking counts of **0, 0, 1** for Friday → Monday → yes. The browser text demo uses today's date. A weekday means its next occurrence after today, in Asia/Kolkata. ISO dates are also accepted.

On Windows, use `py` instead of `python3`, and `Copy-Item .env.example .env` instead of `cp`. If Python lacks the IANA timezone database, install it with `py -m pip install tzdata`.

## Booking and voice rules

- Availability comes from SQLite rows; booked slots disappear from the query.
- A new date request increments the revision and discards the old offer, confirmation, and playout receipt.
- Exact time preferences are stored and checked by both availability and booking. A date-only correction preserves the time; “any time” clears it. An unavailable time requires a new patient choice.
- Offers and success messages are spoken from validated backend values, using `session.say()`.
- Cancelling an offer, interrupting it, or a provider error prevents its playout receipt.
- An authenticated agent callback records completed playout. The browser cannot fabricate that callback or submit voice confirmations directly.
- Confirmation eligibility is captured when user speech starts, before STT finalization. “Is that Monday?” and “What time is that?” preserve the completed offer without approving it; a correction invalidates it.
- A stable operation key, transaction, and unique constraints prevent duplicate bookings for one session or slot.
- A committed booking survives interruption and reconnect. Rescheduling is a separate operation.

SDK playout completion is a software signal, not proof that the person heard audio through their device. The live acceptance checks below include browser audio and physical listening.

## Tests and live acceptance

```bash
uv run --extra voice -m unittest discover -s tests -v
npm ci
npm test
```

The GitHub Actions workflow runs both suites, including the real SDK tests. Running the Python suite without the optional dependencies skips the SDK and JWT tests explicitly.

For the browser checks:

```bash
uv sync --locked --extra voice --extra browser-test
uv run --extra voice --extra browser-test playwright install chromium
uv run --extra voice --extra browser-test scripts/browser_smoke.py
```

To record the same typed walkthrough yourself, install Playwright's video encoder and pass an output directory:

```bash
uv run --extra voice --extra browser-test playwright install ffmpeg
uv run --extra voice --extra browser-test scripts/browser_smoke.py --record-dir artifacts/walkthrough
```

This script uses a temporary database and deliberately runs without LiveKit credentials. The microphone recording procedure is in [voice validation](docs/voice_validation.md).

[The voice acceptance guide](docs/voice_validation.md) specifies corrections, early confirmation, interrupted offers, backchannels, reconnects, and latency collection. Complete it against your real microphone and provider account before describing the project as live-validated.

Server response timing measures agent state transitions on one server clock. Server interruption timing measures detected overlap to the agent leaving its speaking state. Neither includes browser jitter-buffer or speaker delay. Raw SDK per-turn metrics are retained in the exported trace. No measured cloud latency is reported in this repository yet.

## Get a latency summary

After real voice trials, click **Download voice trace** and run:

```bash
python3 latency_report.py ~/Downloads/clinic-voice-trace.json
```

Pass multiple downloaded JSON files to aggregate them. The report separates SDK end-to-end timings from server state intervals, shows sample counts, and calculates p50 (the middle) and p95 (the slow end). Missing timings remain null. These values do not include physical speaker delay.

First prove complete voice bookings, corrections, interruptions, and retries. Recording a working demonstration is enough for the first tutorial. Latency measurements help diagnose whether pauses or stopping feel slow; optimization comes after you have real samples.

## Files

| File | Purpose |
|---|---|
| `booking.py` | Request revisions, availability, proposals, confirmation, playout receipts, atomic booking |
| `server.py` | Local API, token endpoint, and page server |
| `voice_gateway.py` | Short-lived room tokens, agent dispatch, callback authorization |
| `voice_agent.py` | LiveKit streaming speech agent and canonical tool speech |
| `voice_flow.py` | Speech-start confirmation guard, interruption handling, and event timing |
| `latency_report.py` | Sample counts and p50/p95 from exported voice events |
| `web/voice_client.js` | Room connection, microphone publication, and remote audio playback |
| `web/app.js` | Chat, booking state, voice controls, and trace export |
| `tests/` | Backend, HTTP, voice policy, SDK, and client checks |
| `scripts/browser_smoke.py` | Real-browser booking, reload, client setup error, and mobile checks |
| `docs/tutorial.md` | Full blog/tutorial and private GitHub instructions |
| `docs/voice_validation.md` | Live test procedure and evidence limits |

## Scope

All doctors and availability are fictional. English weekdays, ISO dates and exact times such as “10 AM”, “ten a.m.” and “14:00” are supported. This is not a general natural-language date parser. Negated choices, time ranges and ambiguous corrections ask for clarification. Doctor preferences, slot holds, rescheduling, real patient identity and external clinic systems are outside this demo. One session represents one fictional patient and one booking conversation.

The server binds to localhost. Keep the repository private until you choose to publish it.
