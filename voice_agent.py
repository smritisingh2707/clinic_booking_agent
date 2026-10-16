"""Streaming LiveKit agent for the custom browser client and console mode."""
import asyncio
import json
import logging
import os
import time
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from dotenv import load_dotenv
import onnxruntime

# Silero uses ONNX Runtime locally; this demo does not need its telemetry.
onnxruntime.disable_telemetry_events()

from livekit import agents
from livekit.agents import (
    Agent, AgentServer, AgentSession, ChatContext, ChatMessage, StopResponse,
    TurnHandlingOptions, function_tool, inference,
)
from livekit.plugins import silero

from voice_flow import VoiceFlow
from voice_gateway import agent_key

load_dotenv(".env")
BASE = os.environ.get("BOOKING_API_URL", "http://127.0.0.1:8000")
logger = logging.getLogger("clinic.voice")


def request(path, data=None):
    headers = {"Content-Type": "application/json"} if data is not None else {}
    secret = os.environ.get("LIVEKIT_API_SECRET", "")
    if secret:
        headers["X-Voice-Agent-Key"] = agent_key(secret)
    payload = json.dumps(data).encode() if data is not None else None
    try:
        with urlopen(Request(BASE + path, data=payload, headers=headers), timeout=10) as response:
            return json.load(response)
    except HTTPError as exc:
        return {"error": json.load(exc).get("error", str(exc))}
    except (URLError, TimeoutError):
        return {"error": "The booking service could not be reached. Do not report booking success."}


class ClinicAgent(Agent):
    def __init__(self, session_id, emit):
        super().__init__(instructions=(
            "You book appointments at a fictional dermatologist clinic. Reply briefly in English. "
            "The service observes the actual patient turn before you reply; its state is authoritative. "
            "After a date request or correction, call find_availability then offer_appointment "
            "using a returned slot ID and revision. The offer tool speaks the exact validated offer. "
            "The service preserves exact time preferences across date changes. Only offer an ID "
            "from slots, never from alternatives. If slots is empty, explain what is unavailable "
            "and ask the patient to choose an alternative; their new turn must update the request first. "
            "Do not speak a preliminary offer yourself. Never invent a date, doctor, time or reference. "
            "When confirmation is recorded in the service state, call create_booking. That tool "
            "speaks the actual result. If confirmation is rejected because the patient spoke too early, "
            "offer the current appointment again and ask them to wait until you finish before saying yes. "
            "Do not call create_booking when the user corrected the date or a tool returned an error. "
            "For a clarification about the current offer's time, date or doctor, answer from "
            "the stored offer in the service state using IST. Keep that offer unchanged "
            "and wait for explicit confirmation. "
            "If there is already a booking, explain that rescheduling is a separate operation. "
            "Only talk about administrative mock booking."
        ))
        self.flow = VoiceFlow(session_id, self.call, emit)

    async def call(self, path, args=None):
        return await asyncio.to_thread(request, path, args)

    async def on_enter(self):
        await self.flow.initialize()

    async def on_user_turn_completed(self, turn_ctx: ChatContext, new_message: ChatMessage):
        observed = await self.flow.observe(new_message.text_content or "")
        turn_ctx.add_message(role="assistant", content="Booking service state: " + json.dumps(observed))

    @function_tool()
    async def find_availability(self) -> dict:
        """Read slots matching the current date/time; alternatives need a new patient choice."""
        return await self.flow.find_availability()

    @function_tool()
    async def offer_appointment(self, slot_id: str, revision: int) -> dict:
        """Read one validated offer aloud, then record uninterrupted playout completion."""
        result = await self.flow.speak_offer(slot_id, revision, self.session.say)
        if "error" in result:
            return result
        # The offer is already spoken. Do not let the LLM repeat or paraphrase it.
        raise StopResponse()

    @function_tool()
    async def create_booking(self) -> dict:
        """Book a current confirmed spoken offer, then read the actual reference."""
        result = await self.flow.book()
        if "error" in result:
            return result
        stamp = datetime.fromisoformat(result["starts_at"])
        text = (f"Booked with {result['doctor']} on {stamp.strftime('%A, %d %B at %I:%M %p')} IST. "
                f"Your reference is {result['id']}.")
        await self.flow.emit("assistant_message", text=text)
        await self.session.say(text).wait_for_playout()
        raise StopResponse()


server = AgentServer()


@server.rtc_session(agent_name="clinic-booking")
async def entrypoint(ctx: agents.JobContext):
    metadata = json.loads(ctx.job.metadata or "{}")
    session_id = metadata.get("session_id")
    if not session_id:
        state = await asyncio.to_thread(request, "/api/session", {"voice": True})
        if "error" in state:
            raise RuntimeError(state["error"])
        session_id = state["session_id"]
    await ctx.connect()
    tasks = set()

    async def emit(kind, **data):
        packet = {"type": kind, "time": time.time(), "session_id": session_id, **data}
        if "state" in packet:
            packet["state"] = {k: v for k, v in packet["state"].items() if k != "events"}
        logger.info("voice_event %s", json.dumps(packet))
        if ctx.room.isconnected():
            await ctx.room.local_participant.publish_data(
                json.dumps(packet).encode(), reliable=True, topic="clinic.events")

    def schedule(coro):
        task = asyncio.create_task(coro)
        tasks.add(task)
        def done(completed):
            tasks.discard(completed)
            if not completed.cancelled() and completed.exception():
                logger.error("Voice telemetry failed: %s", type(completed.exception()).__name__)
        task.add_done_callback(done)

    agent = ClinicAgent(session_id, emit)
    session = AgentSession(
        stt=inference.STT(model="deepgram/nova-3", language="multi"),
        llm=inference.LLM(model="google/gemma-4-31b-it"),
        tts=inference.TTS(model="fishaudio/s2.1-pro", voice="fa4c9eb3dccc4806b382b40d61c6b10a"),
        vad=silero.VAD.load(),
        turn_handling=TurnHandlingOptions(
            turn_detection=inference.TurnDetector(),
            endpointing={"mode": "fixed", "min_delay": .65, "max_delay": 2.0},
            interruption={"mode": "adaptive"},
            preemptive_generation={"enabled": False},
        ),
    )

    @session.on("user_state_changed")
    def user_state(event):
        if event.new_state == "speaking":
            agent.flow.user_started()
        elif event.old_state == "speaking":
            agent.flow.user_stopped()

    @session.on("agent_state_changed")
    def agent_state(event):
        schedule(agent.flow.agent_changed(event.new_state))

    @session.on("conversation_item_added")
    def item_added(event):
        item = event.item
        if item.role == "assistant":
            schedule(emit("transcript", text=item.text_content, interrupted=item.interrupted))
        metrics = item.metrics.model_dump(exclude_none=True) if hasattr(item.metrics, "model_dump") else dict(item.metrics)
        if metrics:
            schedule(emit("sdk_metrics", metrics=metrics, message_id=item.id))

    @session.on("agent_false_interruption")
    def false_interruption(event):
        schedule(emit("false_interruption"))

    @session.on("error")
    def provider_error(event):
        agent.flow.provider_failed()
        schedule(emit("provider_error", message="A voice provider failed. Inspect the agent log."))

    await session.start(room=ctx.room, agent=agent)
    await session.say("Which day would you like to see a dermatologist?").wait_for_playout()


if __name__ == "__main__":
    agents.cli.run_app(server)
