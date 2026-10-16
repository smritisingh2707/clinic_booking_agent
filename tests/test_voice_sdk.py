"""Exercise the real LiveKit session, tool scheduler, TTS frames and audio buffer.
Providers and the output device are deterministic fixtures; no cloud connection.
"""
import asyncio
import importlib.util
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from booking import BookingError, LOCAL, Store

try:
    from livekit.agents import AgentSession, llm, tts, TurnHandlingOptions
    from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS
    from livekit.agents.voice import io
    from voice_agent import ClinicAgent
    SDK_AVAILABLE = True
except ImportError:
    SDK_AVAILABLE = False


if SDK_AVAILABLE:
    class ToneTTS(tts.TTS):
        def __init__(self):
            super().__init__(capabilities=tts.TTSCapabilities(streaming=False),sample_rate=24000,num_channels=1)
        def synthesize(self,text,*,conn_options=DEFAULT_API_CONNECT_OPTIONS):
            return ToneStream(tts=self,input_text=text,conn_options=conn_options)

    class ToneStream(tts.ChunkedStream):
        async def _run(self,output_emitter):
            output_emitter.initialize(request_id='fixture',sample_rate=24000,num_channels=1,
                                      mime_type='audio/pcm',frame_size_ms=20)
            output_emitter.push(bytes(24000 * 2 // 2))
            output_emitter.end_input()

    class ScriptedLLM(llm.LLM):
        def __init__(self,steps):
            super().__init__()
            self.steps=list(steps)
        def chat(self,*,chat_ctx,tools=None,conn_options=DEFAULT_API_CONNECT_OPTIONS,**kwargs):
            return ScriptedStream(self,chat_ctx=chat_ctx,tools=tools or [],conn_options=conn_options)

    class ScriptedStream(llm.LLMStream):
        async def _run(self):
            if not self._llm.steps:
                return
            name,args=self._llm.steps.pop(0)
            self._event_ch.send_nowait(llm.ChatChunk(id='fixture',delta=llm.ChoiceDelta(role='assistant',
                tool_calls=[llm.FunctionToolCall(name=name,arguments=json.dumps(args),call_id=name)])))

    class ControlledOutput(io.AudioOutput):
        def __init__(self,automatic=False):
            super().__init__(label='test-device',capabilities=io.AudioOutputCapabilities(pause=False),sample_rate=24000)
            self.automatic=automatic
            self.flushed=asyncio.Event()
            self.frames=0
            self.cleared=0
            self.samples=0
        async def capture_frame(self,frame):
            await super().capture_frame(frame)
            self.frames+=1
            self.samples+=frame.samples_per_channel
        def flush(self):
            super().flush()
            self.flushed.set()
            if self.automatic:self.finish()
        def clear_buffer(self):
            self.cleared+=1
            super().flush()
            if self._pending_playback_count:
                self.on_playback_finished(playback_position=0,interrupted=True)
        def finish(self):
            if self._pending_playback_count:
                self.on_playback_finished(playback_position=self.samples/24000,interrupted=False)


@unittest.skipUnless(SDK_AVAILABLE,'Install the optional voice dependencies to run SDK integration tests')
class LiveKitSDKTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.temp.name)/'sdk.sqlite',now=lambda:datetime(2026,10,14,12,tzinfo=LOCAL))
        self.sid=self.store.new_session(voice=True)['session_id']
        self.events=[]
        parent=self
        class LocalAgent(ClinicAgent):
            async def call(self,path,args=None):
                return await parent.call(path,args)
        self.agent=LocalAgent(self.sid,self.emit)
        self.session=None
    async def asyncTearDown(self):
        if self.session:await self.session.aclose()
        self.temp.cleanup()
    async def emit(self,kind,**data):
        self.events.append({'type':kind,**data})
    async def call(self,path,args=None):
        try:
            if path.startswith('/api/session'):return self.store.snapshot(self.sid)
            if path=='/api/observe':return self.store.observe(self.sid,args['text'],args['turn_id'],args['sequence'],confirmation_proposal=args['proposal_id'])
            if path=='/api/availability':return self.store.availability(self.sid,args['revision'])
            if path=='/api/propose':return self.store.propose(self.sid,args['slot_id'],args['revision'])
            if path=='/api/voice/played':return self.store.mark_played(self.sid,args['proposal_id'],args['revision'])
            if path=='/api/book':return self.store.create_booking(self.sid,args['proposal_id'],args['operation_key'])
            raise AssertionError(path)
        except BookingError as exc:return {'error':str(exc)}
    async def start(self,steps,automatic=False):
        self.output=ControlledOutput(automatic)
        self.model=ScriptedLLM(steps)
        self.session=AgentSession(llm=self.model,tts=ToneTTS(),turn_handling=TurnHandlingOptions(turn_detection='manual',preemptive_generation={'enabled':False}))
        self.session.output.audio=self.output
        await self.session.start(agent=self.agent)

    async def test_actual_tool_scheduler_waits_for_playout_before_accepting_confirmation(self):
        await self.start([('find_availability',{}),('offer_appointment',{'slot_id':'2026-10-19-9','revision':1})])
        self.agent.flow.user_started()
        # SDK run(text) bypasses the audio turn hook; explicitly exercise that hook.
        await self.agent.on_user_turn_completed(self.agent.chat_ctx.copy(),llm.ChatMessage(role='user',content=['Monday']))
        response=self.session.run(user_input='Monday')
        await asyncio.wait_for(self.output.flushed.wait(),5)
        self.assertGreater(self.output.frames,0)
        self.assertIsNone(self.store.snapshot(self.sid)['played_proposal'])
        self.output.automatic=True
        self.output.finish()
        await asyncio.wait_for(response,5)
        state=self.store.snapshot(self.sid)
        self.assertEqual(state['played_proposal'],state['proposal_id'])
        self.model.steps=[('create_booking',{})]
        self.agent.flow.user_started()
        await self.agent.on_user_turn_completed(self.agent.chat_ctx.copy(),llm.ChatMessage(role='user',content=['yes']))
        await asyncio.wait_for(self.session.run(user_input='yes'),5)
        self.assertEqual(self.store.snapshot(self.sid)['booking_count'],1)

    async def test_actual_speech_handle_interruption_clears_audio_and_blocks_approval(self):
        await self.start([])
        self.agent.flow.user_started()
        await self.agent.flow.observe('Monday')
        slots=await self.agent.flow.find_availability()
        handles=[]
        def say(text):
            handle=self.session.say(text)
            handles.append(handle)
            return handle
        offer=asyncio.create_task(self.agent.flow.speak_offer(slots['slots'][0]['id'],slots['revision'],say))
        await asyncio.wait_for(self.output.flushed.wait(),5)
        handles[0].interrupt()
        result=await asyncio.wait_for(offer,5)
        self.assertIn('error',result)
        self.assertGreater(self.output.cleared,0)
        self.assertIsNone(self.store.snapshot(self.sid)['played_proposal'])
        self.assertEqual(self.store.snapshot(self.sid)['booking_count'],0)
