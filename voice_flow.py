"""Voice turn rules, independent of the transport and model providers."""
import asyncio
import time
import uuid
from datetime import datetime


class VoiceFlow:
    def __init__(self, session_id, call, emit, clock=time.perf_counter):
        self.session_id, self.call, self.emit, self.clock = session_id, call, emit, clock
        self.sequence = 0
        self.proposal = None
        self.completed_proposal = None
        self.confirmation_at_start = None
        self.agent_state = "initializing"
        self.user_end = None
        self.interruption_start = None
        self.response_recorded = -1
        self.provider_error_epoch = 0

    def provider_failed(self):
        self.provider_error_epoch += 1
        self.completed_proposal = None

    async def initialize(self):
        state = await self.call("/api/session?id=" + self.session_id)
        if "error" in state:
            raise RuntimeError(state["error"])
        self.sequence = state["last_sequence"]
        self.proposal = state["proposal_id"]
        await self.emit("booking_state", state=state)

    def user_started(self):
        # Eligibility is captured when speech starts, before the slower STT final.
        self.confirmation_at_start = self.completed_proposal
        if self.agent_state == "speaking":
            self.interruption_start = self.clock()

    def user_stopped(self):
        self.user_end = self.clock()

    async def agent_changed(self, new_state):
        old_state, self.agent_state = self.agent_state, new_state
        await self.emit("agent_state", state=new_state)
        if new_state == "speaking" and self.user_end is not None and self.response_recorded != self.sequence:
            self.response_recorded = self.sequence
            await self.emit("latency", sequence=self.sequence,
                            server_response_ms=round((self.clock() - self.user_end) * 1000, 1))
        if old_state == "speaking" and new_state != "speaking" and self.interruption_start is not None:
            await self.emit("latency", sequence=self.sequence,
                            server_interruption_ms=round((self.clock() - self.interruption_start) * 1000, 1))
            self.interruption_start = None

    async def observe(self, text):
        self.sequence += 1
        sequence = self.sequence
        eligible = self.confirmation_at_start
        self.confirmation_at_start = None
        completed_before = self.completed_proposal
        error_epoch = self.provider_error_epoch
        # Clear before awaiting HTTP: a later turn cannot approve an old offer.
        self.completed_proposal = None
        observed = await self.call("/api/observe", {
            "session_id": self.session_id, "text": text,
            "turn_id": str(uuid.uuid4()), "sequence": sequence,
            "proposal_id": eligible,
        })
        if sequence != self.sequence:
            return {"error": "A newer patient turn superseded this result."}
        if "error" not in observed:
            self.proposal = observed.get("proposal_id")
            # A clarification keeps a fully played, unchanged offer eligible.
            # A correction, decline, or provider error must not restore that proof.
            if (completed_before and observed.get("proposal_id") == completed_before
                    and observed.get("played_proposal") == completed_before
                    and error_epoch == self.provider_error_epoch):
                self.completed_proposal = completed_before
            await self.emit("booking_state", state=observed)
        await self.emit("user_turn", text=text, sequence=sequence)
        return observed

    async def find_availability(self):
        state = await self.call("/api/session?id=" + self.session_id)
        if "error" in state:
            return state
        return await self.call("/api/availability", {
            "session_id": self.session_id, "revision": state["revision"],
        })

    async def speak_offer(self, slot_id, revision, say):
        sequence = self.sequence
        result = await self.call("/api/propose", {
            "session_id": self.session_id, "slot_id": slot_id, "revision": revision,
        })
        if "error" in result or sequence != self.sequence:
            return result if "error" in result else {"error": "Discard this old offer."}
        self.proposal = result["proposal_id"]
        self.completed_proposal = None
        slot = result["offer"]
        stamp = datetime.fromisoformat(slot["starts_at"])
        text = (f"{slot['doctor']} has {stamp.strftime('%A, %d %B at %I:%M %p')} IST available. "
                "Shall I book that? Please say yes after I finish speaking.")
        await self.emit("booking_state", state=result)
        await self.emit("offer_started", text=text, proposal_id=self.proposal)
        error_epoch = self.provider_error_epoch
        handle = say(text)
        try:
            await handle.wait_for_playout()
        except asyncio.CancelledError:
            handle.interrupt()
            await self.emit("offer_interrupted", proposal_id=result["proposal_id"])
            raise
        if (handle.interrupted or handle.exception() is not None
                or error_epoch != self.provider_error_epoch or sequence != self.sequence):
            await self.emit("offer_interrupted", proposal_id=result["proposal_id"])
            return {"error": "The offer was interrupted. Ask for and read the current offer again."}
        state = await self.call("/api/voice/played", {
            "session_id": self.session_id, "proposal_id": result["proposal_id"],
            "revision": revision,
        })
        if "error" in state or sequence != self.sequence:
            return state if "error" in state else {"error": "Discard this old offer."}
        self.completed_proposal = result["proposal_id"]
        await self.emit("offer_completed", proposal_id=self.completed_proposal)
        await self.emit("booking_state", state=state)
        return state

    async def book(self):
        if not self.proposal:
            return {"error": "No current offer. Check availability and read the offer first."}
        result = await self.call("/api/book", {
            "session_id": self.session_id, "proposal_id": self.proposal,
            "operation_key": "booking:" + self.proposal,
        })
        if "error" not in result:
            await self.emit("booking_created", booking=result)
            state = await self.call("/api/session?id=" + self.session_id)
            if "error" not in state:
                await self.emit("booking_state", state=state)
        return result
