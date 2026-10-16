import asyncio
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from booking import BookingError, LOCAL, Store
from voice_flow import VoiceFlow


class ControlledSpeech:
    def __init__(self):
        self.done = asyncio.Event()
        self.interrupted = False

    async def wait_for_playout(self):
        await self.done.wait()

    def interrupt(self):
        self.interrupted = True
        self.done.set()

    def finish(self):
        self.done.set()

    def exception(self):
        return None


class VoiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'test.sqlite',
                           now=lambda: datetime(2026,10,14,12,tzinfo=LOCAL))
        self.sid = self.store.new_session(voice=True)['session_id']
        self.events = []
        self.time = 10.0
        self.flow = VoiceFlow(self.sid, self.call, self.emit, clock=lambda:self.time)
        await self.flow.initialize()

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def emit(self, kind, **data):
        self.events.append({'type':kind, **data})

    async def call(self, path, data=None):
        try:
            if path.startswith('/api/session'):
                return self.store.snapshot(self.sid)
            if path == '/api/observe':
                return self.store.observe(self.sid,data['text'],data['turn_id'],data['sequence'],
                                          confirmation_proposal=data['proposal_id'])
            if path == '/api/availability':
                return self.store.availability(self.sid,data['revision'])
            if path == '/api/propose':
                return self.store.propose(self.sid,data['slot_id'],data['revision'])
            if path == '/api/voice/played':
                return self.store.mark_played(self.sid,data['proposal_id'],data['revision'])
            if path == '/api/book':
                return self.store.create_booking(self.sid,data['proposal_id'],data['operation_key'])
            raise AssertionError(path)
        except BookingError as exc:
            return {'error':str(exc)}

    async def say(self, text):
        self.flow.user_started()
        return await self.flow.observe(text)

    async def start_offer(self):
        slots = await self.flow.find_availability()
        speech = ControlledSpeech()
        task = asyncio.create_task(self.flow.speak_offer(slots['slots'][0]['id'],slots['revision'],lambda text:speech))
        # Synchronize on the actual proposal instead of a timing assumption.
        for _ in range(20):
            if self.store.snapshot(self.sid)['proposal_id']:
                break
            await asyncio.sleep(0)
        self.assertIsNotNone(self.store.snapshot(self.sid)['proposal_id'])
        return speech, task

    async def complete_offer(self):
        speech, task = await self.start_offer()
        speech.finish()
        return await task

    async def test_voice_yes_before_playout_cannot_book(self):
        await self.say('Monday')
        speech, task = await self.start_offer()
        result = await self.say('yes')
        self.assertIn('error',result)
        self.assertIn('error',await self.flow.book())
        speech.finish()
        await task
        self.assertEqual(self.store.snapshot(self.sid)['booking_count'],0)

    async def test_yes_started_early_is_rejected_even_when_stt_final_arrives_late(self):
        await self.say('Monday')
        speech, task = await self.start_offer()
        self.flow.user_started()  # Says yes before the offer ends.
        speech.finish()
        self.assertNotIn('error',await task)
        result = await self.flow.observe('yes')  # STT final arrives after playback.
        self.assertIn('error',result)
        self.assertIn('error',await self.flow.book())
        self.assertEqual(self.store.snapshot(self.sid)['booking_count'],0)

    async def test_finished_current_offer_then_yes_creates_one_booking(self):
        await self.say('Friday actually Monday')
        offer = await self.complete_offer()
        self.assertEqual(offer['played_proposal'],offer['proposal_id'])
        confirmed = await self.say('yes')
        self.assertEqual(confirmed['confirmed_proposal'],offer['proposal_id'])
        first, second = await self.flow.book(), await self.flow.book()
        self.assertEqual(first['id'],second['id'])
        self.assertTrue(first['starts_at'].startswith('2026-10-19'))
        self.assertEqual(self.store.snapshot(self.sid)['booking_count'],1)

    async def test_clarifying_time_preserves_completed_offer_for_next_yes(self):
        await self.say('Monday')
        offer = await self.complete_offer()
        clarified = await self.say('What time is that?')
        self.assertEqual(clarified['proposal_id'],offer['proposal_id'])
        self.assertEqual(self.flow.completed_proposal,offer['proposal_id'])
        await self.say('yes')
        booked = await self.flow.book()
        self.assertNotIn('error',booked)
        self.assertEqual(booked['proposal_id'],offer['proposal_id'])
        self.assertEqual(self.store.snapshot(self.sid)['booking_count'],1)

    async def test_clarifying_date_and_time_keeps_spoken_offer_confirmable(self):
        await self.say('Friday at ten a.m. actually Monday')
        offer = await self.complete_offer()
        clarified = await self.say('Is that Monday at 10 am?')
        self.assertEqual(clarified['revision'], offer['revision'])
        self.assertEqual(clarified['proposal_id'], offer['proposal_id'])
        self.assertEqual(self.flow.completed_proposal, offer['proposal_id'])
        self.assertIsNone(clarified['confirmed_proposal'])
        await self.say('yes')
        first, retry = await self.flow.book(), await self.flow.book()
        self.assertEqual(first['id'], retry['id'])
        self.assertIn('2026-10-19T10:00:', first['starts_at'])
        self.assertEqual(self.store.snapshot(self.sid)['booking_count'], 1)

    async def test_time_correction_needs_a_new_completed_spoken_offer(self):
        await self.say('Monday at 9 AM')
        old = await self.complete_offer()
        await self.say('Actually 10 AM')
        self.assertIsNone(self.flow.completed_proposal)
        early = await self.say('yes')
        self.assertIsNone(early['confirmed_proposal'])
        self.assertEqual(early['booking_count'], 0)
        self.assertIn('error', await self.flow.book())
        revised = await self.complete_offer()
        self.assertNotEqual(revised['proposal_id'], old['proposal_id'])
        await self.say('yes')
        booked = await self.flow.book()
        self.assertIn('T10:00:', booked['starts_at'])

    async def test_correction_after_clarification_still_requires_new_spoken_offer(self):
        await self.say('Monday')
        old = await self.complete_offer()
        await self.say('What time is that?')
        await self.say('Actually Friday')
        self.assertIsNone(self.flow.completed_proposal)
        self.assertIsNone(self.store.snapshot(self.sid)['played_proposal'])
        self.assertIn('error',await self.flow.book())
        with self.assertRaises(BookingError):
            self.store.create_booking(self.sid,old['proposal_id'])

    async def test_provider_failure_during_clarification_does_not_restore_approval(self):
        await self.say('Monday')
        await self.complete_offer()
        original = self.flow.call
        async def fail_after_read(path,data=None):
            result = await original(path,data)
            if path == '/api/observe':
                self.flow.provider_failed()
            return result
        self.flow.call = fail_after_read
        await self.say('What time is that?')
        self.assertIsNone(self.flow.completed_proposal)
        self.flow.call = original
        self.assertIn('error',await self.say('yes'))
        self.assertEqual(self.store.snapshot(self.sid)['booking_count'],0)

    async def test_correction_interrupts_old_offer_and_new_offer_requires_playout(self):
        await self.say('Friday')
        speech, task = await self.start_offer()
        friday = self.store.snapshot(self.sid)['proposal_id']
        speech.interrupt()
        await self.say('Actually Monday')
        result = await task
        self.assertIn('error',result)
        self.assertIsNone(self.store.snapshot(self.sid)['played_proposal'])
        monday = await self.complete_offer()
        self.assertNotEqual(friday,monday['proposal_id'])
        await self.say('yes')
        booked = await self.flow.book()
        self.assertTrue(booked['starts_at'].startswith('2026-10-19'))

    async def test_cancelling_speech_cannot_mark_offer_played(self):
        await self.say('Monday')
        speech, task = await self.start_offer()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(speech.interrupted)
        self.assertIsNone(self.store.snapshot(self.sid)['played_proposal'])

    async def test_late_old_playout_receipt_is_rejected(self):
        await self.say('Friday')
        old = await self.complete_offer()
        await self.say('Monday')
        with self.assertRaises(BookingError):
            self.store.mark_played(self.sid,old['proposal_id'],old['revision'])

    async def test_provider_failure_cannot_authorize_offer(self):
        await self.say('Monday')
        speech, task = await self.start_offer()
        self.flow.provider_failed()
        speech.finish()
        self.assertIn('error',await task)
        self.assertIsNone(self.store.snapshot(self.sid)['played_proposal'])

    async def test_backchannel_never_creates_booking(self):
        await self.say('Monday')
        await self.complete_offer()
        await self.say('haan')
        self.assertIn('error',await self.flow.book())
        self.assertEqual(self.store.snapshot(self.sid)['booking_count'],0)

    async def test_server_latency_is_correlated_once_per_turn(self):
        await self.say('Monday')
        self.time = 10
        self.flow.user_stopped()
        self.time = 10.35
        await self.flow.agent_changed('speaking')
        await self.flow.agent_changed('thinking')
        self.time = 11
        await self.flow.agent_changed('speaking')
        samples = [e for e in self.events if 'server_response_ms' in e]
        self.assertEqual(len(samples),1)
        self.assertEqual(samples[0]['server_response_ms'],350)
        self.assertEqual(samples[0]['sequence'],self.flow.sequence)

    async def test_interruption_latency_uses_same_server_clock(self):
        await self.flow.agent_changed('speaking')
        self.time = 12
        self.flow.user_started()
        self.time = 12.08
        await self.flow.agent_changed('listening')
        samples = [e for e in self.events if 'server_interruption_ms' in e]
        self.assertEqual(samples[0]['server_interruption_ms'],80)

    async def test_reconnect_restores_booking_and_turn_sequence(self):
        await self.say('Monday')
        await self.complete_offer()
        await self.say('yes')
        booked = await self.flow.book()
        restored = VoiceFlow(self.sid,self.call,self.emit)
        await restored.initialize()
        self.assertEqual(restored.sequence,2)
        self.assertIsNone(restored.completed_proposal)
        self.assertEqual((await restored.book())['id'],booked['id'])
