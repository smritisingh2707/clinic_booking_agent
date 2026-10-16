# Validate the real voice path

The automated tests exercise booking rules, HTTP authorization, the real LiveKit SDK's tool/speech scheduling and audio-buffer cancellation, and the JavaScript client lifecycle. The model providers and audio output in SDK tests are deterministic fixtures. They do not establish cloud STT accuracy, acoustic turn-taking, browser playback latency, or microphone reliability.

## Start the actual browser and agent

1. Fill the three LiveKit settings in `.env` using your own project.
2. Run `uv run --extra voice server.py` in terminal 1.
3. Run `uv run --extra voice voice_agent.py download-files`, then `uv run --extra voice voice_agent.py dev` in terminal 2.
4. Open `http://127.0.0.1:8000` in Chrome or another WebRTC browser. Click **Connect voice**, grant microphone access, and enable audio if prompted.
5. Confirm that you can hear the greeting. Speak a weekday and confirm that its transcript and a stored clinic offer appear.

Use fictional patient data and headphones. Each test begins with **New session** so bookings from previous trials do not consume the same patient's session.

## Run these cases

| Case | Speak/do | What must happen |
|---|---|---|
| Correction within a turn | “Friday…” pause 650 ms, then “…actually Monday” | Final offer is Monday. No Friday booking exists. |
| Correction during playback | While a Friday offer plays, say “Wait, actually Monday” | Old audio stops; Friday approval is blocked; a new Monday offer is read. |
| Early yes | Say “yes” while the doctor/date/time offer is still playing | No booking. The agent reoffers the current slot and asks for confirmation after completion. |
| Clarification | After the full offer, ask “What time is that?”, then say “yes” | The same current offer remains confirmable; one booking. |
| Date clarification | After the full Monday offer, ask “Is that Monday?”, then say “yes” | Revision, slot and proposal stay unchanged; one booking after yes. |
| Time correction | Ask for Friday at 10 AM, then change only the day to Monday | Monday at 10 AM is offered if available; a different time requires a new patient choice. |
| Unavailable time | Ask for Monday at 11 AM | No invented or substituted offer. Choose a returned alternative, then confirm its completed offer. |
| Normal confirmation | Wait for the full offer, then say “yes” | One booking with the offered doctor/date/time and a returned booking ID. |
| Backchannel | Say “haan” or “mhm” during an offer | No booking. Check whether adaptive interruption resumes or preserves the offer naturally. |
| Reconnect | After success, disconnect and reconnect; request the same booking again | Same session and booking reference. No second booking. |
| Competing callers | In two browser profiles, choose the same available slot and confirm | One success; the other caller gets a conflict and must receive another offer. |
| Provider/network failure | Disconnect during an offer or temporarily stop the booking server | No unconfirmed booking. A successful prior commit remains visible after recovery. |

The short confirmation whitelist includes English yes/confirm/book-it variants. Hinglish affirmation support requires a separate policy and spoken test set; `haan` intentionally does not commit a booking.

## Collect evidence

After each run, click **Download voice trace**. Keep the agent log and a screen recording with the booking count and current offer visible. The JSON trace contains:

- `user_turn`: a committed transcript, not an interim STT hypothesis.
- `offer_started`, `offer_completed`, `offer_interrupted`: the proposal being spoken and its SDK playout result.
- `booking_state`, `booking_created`: actual backend state and booking ID.
- `latency`: server response/interruption intervals in milliseconds.
- `sdk_metrics`: SDK metrics, including per-turn `e2e_latency` when supplied.
- `provider_error` and `false_interruption`: explicit error/turn-handling signals.

Some tool-driven `say()` calls have no SDK speech-turn ID. Use proposal IDs and the server intervals to correlate those calls instead of inventing a stage measurement.

Start with five journeys: ordinary booking, Friday → Monday correction, interruption during an offer, clarification before yes, and a retry after success. Check the actual booking date and count in every journey. This working evidence matters more for the first tutorial than chasing a latency threshold.

Download the trace and summarize the recorded timings:

```bash
python3 latency_report.py ~/Downloads/clinic-voice-trace.json
```

For several runs, pass all their JSON paths. SDK `e2e_latency` seconds are converted to milliseconds; the server response and interruption intervals are summarized separately. The percentile calculation uses linear interpolation. Missing samples produce null rather than zero.

Collect at least 20 ordinary turns and several intentional interruptions before computing percentiles. Keep separate results for ordinary answers, tool-bearing turns, and interrupted offers. Report the sample count with p50 and p95. A screen recording or listening review is needed for premature cutoff and backchannel mistakes; a low latency number alone does not show correct turn-taking.

Server state intervals exclude the network path to the browser, browser jitter buffering, and the speaker. For perceived stop/response latency, record loopback audio and microphone input together and compare the last user sample to the first/last agent sample. SDK `wait_for_playout()` does not prove the physical speaker was audible.

## Current evidence

| Layer | Status |
|---|---|
| Booking rules and persistence | Automated tests passed |
| Voice callback authorization and real JWT creation | Automated tests passed |
| Actual LiveKit AgentSession tool scheduler, TTS frame handling, and clear-buffer behavior | Automated tests passed with provider/device fixtures |
| JavaScript room/microphone/audio lifecycle | Automated tests passed with a room fixture |
| Real Chromium page, booking/reload, exact time, date clarification, unavailable time, client startup without credentials, mobile layout | Seven checks passed; no page errors |
| Live cloud STT/LLM/TTS | Credentials configured; live startup remains blocked in this environment |
| Physical microphone, remote browser playback, and adaptive acoustic interruptions | Pending live acceptance |
| Measured cloud p50/p95 latency | No live samples collected |

Do not describe fixture-controlled timing values in unit tests as measured voice latency.

The included `typed_walkthrough.mp4` records the actual local browser flow with typed input. Its label remains visible, and it contains no microphone or agent audio.

## Current live startup blocker

The LiveKit API authenticated successfully, but automatic approval review rejected agent startup because it attempted to contact an unrelated Microsoft telemetry destination with unknown contents. Disabling ONNX Runtime telemetry did not resolve the rejection. This release does not retry the blocked action or claim a microphone recording. The local typed walkthrough is labeled separately.

## Record the microphone demonstration

Keep the browser's current offer, booking count and audit trail visible. Capture both your microphone and the agent's audio; verify both tracks in a short playback before recording the full journey. Use fictional data and keep the credentials file and dashboard out of the recording.

1. Begin a new voice session. Ask for Friday at 10 AM.
2. While the offer is playing, say “Wait, actually Monday.” Check that the Friday audio stops and that no booking appears.
3. Let the Monday offer finish. Ask “Is that Monday?” Check that the same offer is repeated.
4. After the answer finishes, say “yes.” Show the stored Monday 10 AM booking and its reference.
5. Say “yes” again, then reconnect. Show the same reference and a count of one.
6. Download the voice trace. Keep it with the unedited recording and note any failed attempts.

A trace plus a recording establishes what happened in that run. Repeat the early-yes and backchannel cases separately before making broader interruption claims.
