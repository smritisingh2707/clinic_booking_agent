# Review of the clinic booking demo

Updated for version 0.3.0 on 16 October 2026. This is a useful tutorial project with a carefully enforced booking contract. It is not a production clinic service, and the live voice path is not yet validated.

## What is worth sharing

The best part is the boundary between conversation and a committed booking. The model selects tools; Python validates the current request, confirmation and availability. SQLite transactions and unique constraints stop a retry from producing a second booking for the same session or slot. A spoken offer must finish before a later confirmation can authorize it. A date correction invalidates the earlier offer. Tests include real LiveKit scheduling and audio-buffer interfaces with deterministic providers.

These are good engineering decisions to explain in a portfolio tutorial. They provide stronger material than showing a successful conversational reply alone. They do not establish production readiness.

## Findings

| Priority | Finding and evidence | What to change |
| --- | --- | --- |
| Before shared hosting | `server.py` has no authenticated user identity or session ownership. A session UUID is effectively a bearer identifier; several routes trust whoever supplies it. The agent callback key protects some voice routes but does not provide user authorization. | Authenticate visitors, bind every session and read/write operation to its owner, and scope agent callbacks to the relevant session. |
| Before a public demo | There is no application rate limit, call-duration limit or explicit spend limit on issuing voice-room tokens. | Add per-user limits, bounded call duration and provider budget controls before inviting other users. |
| Fixed in 0.3.0 | Exact times are stored and enforced by availability, proposals and the final booking check. Day-only corrections preserve the time. Unavailable times return alternatives without silently selecting one. | Regression tests and a real-browser check passed. General natural-language scheduling remains outside scope. |
| Fixed in 0.3.0 | “Is that Monday?” and supported date/time questions preserve the current revision, proposal and completed playout eligibility. A clarification does not authorize a booking. | Backend, voice-flow and browser regression checks passed. The clarification vocabulary is deliberately bounded. |
| Before production reliability | HTTP failures are not handled consistently. The agent has a timeout, but it has no bounded retry/reconciliation strategy for every failed operation, and malformed provider/API responses can escape its narrow exception handling. | Retry safe reads selectively; reuse operation IDs for writes and reconcile the stored booking after an uncertain response. |
| Before sustained operation | The stdlib HTTP server, local SQLite file, startup-only availability seeding, and full event-history reads are intended for a local demonstration. There is no tested deployment, backup/restore process, retention policy or operational alerting. | Choose a supported serving/deployment arrangement, test database recovery and concurrency, replenish availability independently, bound history reads and define retention. SQLite itself is not the reason this is a demo. |
| Before claiming live validation | Automated tests use deterministic speech/model fixtures. No end-to-end microphone conversation, acoustic interruption test or caller-experienced latency distribution has passed here. | Record actual calls with the configured providers. Include correction, interrupted offer, early yes, repeated yes, unavailable slot and reconnect. Report sample counts with any p50/p95. |

## Credential handling

Actual LiveKit connection settings belong only in the ignored server `.env` or a deployment secret store. The shareable `.env.example` contains placeholders. The browser receives an expiring room token; the project API secret stays server-side.

The ignore rules now cover `.env`, `.env.*`, private-key files and certificate files, while permitting `.env.example`. The distribution archive is assembled from an explicit file list and checked against the configured credential values. A Git ignore rule cannot remove a secret from existing Git history; this working project has not been initialized or pushed as a Git repository.

ONNX Runtime telemetry is explicitly disabled before the local speech model is loaded. The agent startup still triggered an automatic approval rejection for a Microsoft telemetry destination. The source of that remaining request and its payload have not been established; telemetry-free startup is not verified.

## Evidence from this review

- Python: 66 tests passed; JavaScript: 6 tests passed; browser: 7 checks passed with no page errors.
- Fixed the ignored 10 AM preference and date-clarification reset; verified time-only corrections, unavailable times and migration of existing databases.
- The included typed browser recording shows local booking and retry behavior. It contains no microphone or provider audio and does not prove a live WebRTC call.
- LiveKit account access and a dedicated demo key are configured. A read-only LiveKit API request authenticated successfully through the configured network proxy. Agent startup remains blocked: automatic approval review rejected an unrelated Microsoft telemetry destination with unknown payload, even after ONNX telemetry was disabled. No further startup attempt was made. Neither account login nor an API check is evidence of working speech.

## How to present the tutorial

A precise promise would be: “Build a small voice booking agent that handles a date correction, asks for confirmation and prevents duplicate bookings.” Show the database state and the reason each guard exists. Describe the supported date/time vocabulary directly. Add the real microphone recording before calling it a working voice demo. Keep production authentication, deployment operations and broader language understanding as clearly identified follow-up work.

You can publish the learning project once its stated demo flow works and the limitations are accurate. You should not label this version “production-ready”, “medical-grade” or proof of general exactly-once delivery. Its duplicate protection applies to the local booking transaction for one session and one slot.
