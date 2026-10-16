import tempfile
import sqlite3
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from booking import BookingError, LOCAL, Store


class BookingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "test.sqlite",
                           now=lambda: datetime(2026, 10, 14, 12, tzinfo=LOCAL))
        self.session = self.store.new_session()["session_id"]

    def tearDown(self):
        self.temp.cleanup()

    def say(self, text, session=None, automatic=True, proposal="current", sequence=None):
        session = session or self.session
        state = self.store.snapshot(session)
        nonce = state["proposal_id"] if proposal == "current" else proposal
        return self.store.observe(session, text, str(uuid.uuid4()),
                                  sequence or state["last_sequence"] + 1,
                                  automatic=automatic, confirmation_proposal=nonce)

    def prepare_voice(self, session=None, day="Monday"):
        session = session or self.session
        state = self.say(day, session=session, automatic=False)
        slots = self.store.availability(session, state["revision"])["slots"]
        offered = self.store.propose(session, slots[0]["id"], state["revision"])
        self.say("yes", session=session, automatic=False, proposal=offered["proposal_id"])
        return offered

    def test_correction_in_one_utterance_books_monday_once(self):
        state = self.say("Book a dermatologist Friday... actually make that Monday")
        self.assertEqual(state["preferred_date"], "2026-10-19")
        self.assertEqual(state["booking_count"], 0)
        booked = self.say("yes")
        self.assertEqual(booked["booking_count"], 1)
        self.assertTrue(booked["booking"]["starts_at"].startswith("2026-10-19"))

    def test_correction_in_later_turn_invalidates_friday_confirmation(self):
        friday = self.say("Friday")
        monday = self.say("Actually Monday")
        self.assertNotEqual(friday["proposal_id"], monday["proposal_id"])
        with self.assertRaises(BookingError):
            self.say("yes", proposal=friday["proposal_id"])
        self.assertEqual(self.store.snapshot(self.session)["booking_count"], 0)
        self.assertEqual(self.say("yes")["booking_count"], 1)

    def test_booking_tool_cannot_supply_its_own_confirmation(self):
        offered = self.say("Monday")
        with self.assertRaises(BookingError):
            self.store.create_booking(self.session, offered["proposal_id"])
        self.assertEqual(self.store.snapshot(self.session)["booking_count"], 0)

    def test_backchannel_is_not_confirmation(self):
        self.say("Monday")
        self.say("haan")
        self.assertEqual(self.store.snapshot(self.session)["booking_count"], 0)

    def test_yes_with_a_correction_does_not_book(self):
        self.say("Friday")
        result = self.say("Yes, but actually make that Monday")
        self.assertEqual(result["preferred_date"], "2026-10-19")
        self.assertEqual(result["booking_count"], 0)

    def test_same_operation_retry_returns_the_same_booking(self):
        offered = self.prepare_voice()
        first = self.store.create_booking(self.session, offered["proposal_id"])
        second = self.store.create_booking(self.session, offered["proposal_id"])
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(self.store.snapshot(self.session)["booking_count"], 1)

    def test_retry_of_same_user_turn_does_not_write_twice(self):
        self.say("Monday")
        proposal = self.store.snapshot(self.session)["proposal_id"]
        turn_id = str(uuid.uuid4())
        first = self.store.observe(self.session, "yes", turn_id, 2, True, proposal)
        second = self.store.observe(self.session, "yes", turn_id, 2, True, proposal)
        self.assertEqual(first["booking"]["id"], second["booking"]["id"])
        self.assertEqual(second["booking_count"], 1)

    def test_old_availability_cannot_offer_after_a_correction(self):
        friday = self.say("Friday", automatic=False)
        old_slots = self.store.availability(self.session, friday["revision"])["slots"]
        self.say("Monday", automatic=False)
        with self.assertRaises(BookingError):
            self.store.propose(self.session, old_slots[0]["id"], friday["revision"])

    def test_out_of_order_user_turn_does_not_restore_friday(self):
        self.say("Monday", sequence=3)
        with self.assertRaises(BookingError):
            self.say("Friday", sequence=2)
        self.assertEqual(self.store.snapshot(self.session)["preferred_date"], "2026-10-19")

    def test_correction_after_confirmation_before_write_invalidates_proof(self):
        old = self.prepare_voice(day="Friday")
        self.say("Monday", automatic=False)
        with self.assertRaises(BookingError):
            self.store.create_booking(self.session, old["proposal_id"])
        self.assertEqual(self.store.snapshot(self.session)["booking_count"], 0)

    def test_two_patients_cannot_take_the_same_slot(self):
        one = self.prepare_voice()
        second_session = self.store.new_session()["session_id"]
        two = self.prepare_voice(session=second_session)
        self.assertEqual(one["offer"]["id"], two["offer"]["id"])
        barrier = threading.Barrier(2)
        def commit(session, proposal):
            barrier.wait()
            try:
                return self.store.create_booking(session, proposal)["id"]
            except BookingError:
                return None
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(lambda pair: commit(*pair),
                                      [(self.session, one["proposal_id"]),
                                       (second_session, two["proposal_id"])]))
        self.assertEqual(sum(value is not None for value in results), 1)

    def test_double_click_confirmation_creates_one_booking(self):
        offer = self.prepare_voice()
        barrier = threading.Barrier(2)
        def commit():
            barrier.wait()
            return self.store.create_booking(self.session, offer["proposal_id"])["id"]
        with ThreadPoolExecutor(max_workers=2) as workers:
            futures = [workers.submit(commit), workers.submit(commit)]
            references = [future.result() for future in futures]
        self.assertEqual(references[0], references[1])
        self.assertEqual(self.store.snapshot(self.session)["booking_count"], 1)

    def test_after_commit_correction_is_not_a_silent_rollback(self):
        self.say("Friday")
        old = self.say("yes")["booking"]
        corrected = self.say("Actually Monday")
        self.assertIn("rescheduling", corrected["message"])
        self.assertEqual(corrected["booking"]["id"], old["id"])
        self.assertEqual(corrected["booking_count"], 1)
        self.assertEqual(corrected["booking"]["starts_at"], old["starts_at"])

    def test_reused_operation_key_cannot_book_a_different_patient(self):
        first = self.prepare_voice()
        self.store.create_booking(self.session, first["proposal_id"], "shared-key")
        other = self.store.new_session()["session_id"]
        second = self.prepare_voice(session=other)
        with self.assertRaises(BookingError):
            self.store.create_booking(other, second["proposal_id"], "shared-key")

    def test_taken_slot_disappears_from_availability(self):
        first = self.prepare_voice()
        slot_id = first["offer"]["id"]
        self.store.create_booking(self.session, first["proposal_id"])
        other = self.store.new_session()["session_id"]
        state = self.say("Monday", session=other, automatic=False)
        slots = self.store.availability(other, state["revision"])["slots"]
        self.assertNotIn(slot_id, [slot["id"] for slot in slots])

    def test_declining_offer_invalidates_it(self):
        offered = self.say("Monday")
        state = self.say("no")
        self.assertIsNone(state["proposal_id"])
        with self.assertRaises(BookingError):
            self.store.create_booking(self.session, offered["proposal_id"])

    def test_turn_retry_must_preserve_the_confirmation_payload(self):
        offered = self.say("Monday")
        turn_id = str(uuid.uuid4())
        self.store.observe(self.session, "yes", turn_id, 2, True, offered["proposal_id"])
        with self.assertRaises(BookingError):
            self.store.observe(self.session, "yes", turn_id, 2, True, "a-different-offer")

    def test_unparsed_correction_revokes_existing_confirmation(self):
        offered = self.prepare_voice(day="Friday")
        state = self.say("Actually make that tomorrow", automatic=False)
        self.assertIsNone(state["confirmed_proposal"])
        self.assertIsNone(state["preferred_date"])
        with self.assertRaises(BookingError):
            self.store.create_booking(self.session, offered["proposal_id"])

    def test_negated_weekday_is_not_treated_as_the_requested_day(self):
        offered = self.say("Monday")
        state = self.say("Not Monday")
        self.assertIsNone(state["proposal_id"])
        with self.assertRaises(BookingError):
            self.store.create_booking(self.session, offered["proposal_id"])

    def test_new_request_for_same_day_still_requires_fresh_confirmation(self):
        old = self.prepare_voice(day="Monday")
        state = self.say("Different doctor on Monday", automatic=False)
        self.assertIsNone(state["confirmed_proposal"])
        with self.assertRaises(BookingError):
            self.store.create_booking(self.session, old["proposal_id"])

    def test_exact_time_is_used_for_offer_and_booking(self):
        offered = self.say("Monday at 10 am")
        self.assertEqual(offered["preferred_time"], "10:00")
        self.assertIn("T10:00:", offered["offer"]["starts_at"])
        booked = self.say("yes")
        self.assertIn("T10:00:", booked["booking"]["starts_at"])
        self.assertEqual(booked["booking_count"], 1)

    def test_day_correction_preserves_time_in_one_or_two_turns(self):
        for first, correction in [("Friday at 10 am actually Monday", None),
                                  ("Friday at 10 am", "Actually Monday")]:
            with self.subTest(first=first):
                sid = self.store.new_session()["session_id"]
                offered = self.say(first, session=sid)
                if correction:
                    offered = self.say(correction, session=sid)
                self.assertEqual(offered["preferred_date"], "2026-10-19")
                self.assertEqual(offered["preferred_time"], "10:00")
                self.assertIn("T10:00:", offered["offer"]["starts_at"])

    def test_time_only_correction_revokes_previous_consent(self):
        old = self.prepare_voice(day="Monday at 9 AM")
        revised = self.say("Actually make that 10 AM", automatic=False)
        self.assertEqual(revised["preferred_date"], "2026-10-19")
        self.assertEqual(revised["preferred_time"], "10:00")
        self.assertIsNone(revised["confirmed_proposal"])
        with self.assertRaises(BookingError):
            self.store.create_booking(self.session, old["proposal_id"])
        slots = self.store.availability(self.session, revised["revision"])["slots"]
        self.assertEqual([slot["id"] for slot in slots], ["2026-10-19-10"])

    def test_wrong_time_cannot_be_proposed_directly(self):
        state = self.say("Monday at 10 am", automatic=False)
        with self.assertRaises(BookingError):
            self.store.propose(self.session, "2026-10-19-9", state["revision"])

    def test_unavailable_time_requires_patient_to_choose_an_alternative(self):
        state = self.say("Monday at 11 AM")
        self.assertIsNone(state["offer"])
        self.assertEqual(state["booking_count"], 0)
        available = self.store.availability(self.session, state["revision"])
        self.assertEqual(available["slots"], [])
        self.assertEqual(len(available["alternatives"]), 3)
        with self.assertRaises(BookingError):
            self.store.propose(self.session, available["alternatives"][0]["id"], state["revision"])
        chosen = self.say("10 am")
        self.assertIn("T10:00:", chosen["offer"]["starts_at"])
        self.assertEqual(self.say("yes")["booking_count"], 1)

    def test_supported_spoken_and_numeric_times(self):
        for wording, expected in [("ten a.m.", "10:00"), ("ten a m", "10:00"),
                                  ("2 pm", "14:00"), ("14:00", "14:00"),
                                  ("ten thirty am", "10:30"), ("noon", "12:00"),
                                  ("12 am", "00:00")]:
            with self.subTest(wording=wording):
                sid = self.store.new_session()["session_id"]
                state = self.say("Monday at " + wording, session=sid)
                self.assertEqual(state["preferred_time"], expected)
                self.assertEqual(state["booking_count"], 0)

    def test_ambiguous_or_invalid_times_do_not_reuse_old_offer(self):
        for wording in ["Monday at 10", "Monday at 25:00", "Monday at 13 pm",
                        "Monday afternoon", "Monday between 9 am and 2 pm"]:
            with self.subTest(wording=wording):
                sid = self.store.new_session()["session_id"]
                old = self.prepare_voice(session=sid)
                state = self.say(wording, session=sid)
                self.assertIsNone(state["proposal_id"])
                self.assertIsNone(state["confirmed_proposal"])
                with self.assertRaises(BookingError):
                    self.store.create_booking(sid, old["proposal_id"])

    def test_any_time_clears_only_time_preference(self):
        self.say("Monday at 10 AM")
        state = self.say("Any time")
        self.assertEqual(state["preferred_date"], "2026-10-19")
        self.assertIsNone(state["preferred_time"])
        self.assertIn("T09:00:", state["offer"]["starts_at"])

    def test_time_given_before_day_is_preserved(self):
        state = self.say("10 AM")
        self.assertIsNone(state["offer"])
        self.assertEqual(state["preferred_time"], "10:00")
        offered = self.say("Monday")
        self.assertIn("T10:00:", offered["offer"]["starts_at"])

    def test_date_and_time_clarifications_preserve_offer_without_confirming(self):
        offered = self.say("Monday at 10 AM")
        for question in ["Is that Monday?", "Is that Friday?", "Did you say Monday at ten?",
                         "What time is that?", "Which doctor is that?"]:
            state = self.say(question)
            self.assertEqual(state["revision"], offered["revision"])
            self.assertEqual(state["proposal_id"], offered["proposal_id"])
            self.assertIsNone(state["confirmed_proposal"])
            self.assertIn("Monday", state["message"])
            self.assertIn("10:00 AM", state["message"])
        self.assertEqual(self.say("yes")["booking_count"], 1)

    def test_request_phrased_as_question_still_changes_the_offer(self):
        first = self.say("Friday at 9 AM")
        changed = self.say("Can you do Monday at 10 AM?")
        self.assertNotEqual(changed["proposal_id"], first["proposal_id"])
        self.assertEqual(changed["preferred_date"], "2026-10-19")
        self.assertEqual(changed["preferred_time"], "10:00")

    def test_correction_after_a_question_still_invalidates_offer(self):
        old = self.say("Monday at 10 AM")
        state = self.say("Is that Monday? Actually make that Friday at 2 pm")
        self.assertNotEqual(state["proposal_id"], old["proposal_id"])
        self.assertEqual(state["preferred_date"], "2026-10-16")
        self.assertEqual(state["preferred_time"], "14:00")

    def test_negated_or_alternative_times_are_not_silently_selected(self):
        for wording in ["Monday but not at 10 AM", "Monday at 9 AM or 10 AM",
                        "10 AM instead of 9 AM", "Don't book Monday at 10 AM"]:
            with self.subTest(wording=wording):
                sid = self.store.new_session()["session_id"]
                self.say("Monday", session=sid)
                state = self.say(wording, session=sid)
                self.assertIsNone(state["proposal_id"])
                self.assertEqual(state["booking_count"], 0)

    def test_existing_database_gains_time_column_without_losing_session(self):
        path = Path(self.temp.name) / "older.sqlite"
        with sqlite3.connect(path) as db:
            db.executescript("""
                CREATE TABLE sessions (
                  id TEXT PRIMARY KEY, preferred_date TEXT, revision INTEGER NOT NULL DEFAULT 0,
                  last_sequence INTEGER NOT NULL DEFAULT 0, proposal_id TEXT, slot_id TEXT,
                  confirmed_proposal TEXT, voice_mode INTEGER NOT NULL DEFAULT 0, played_proposal TEXT);
                INSERT INTO sessions(id, preferred_date, revision) VALUES('existing', '2026-10-19', 1);
            """)
        migrated = Store(path, now=self.store.now)
        state = migrated.snapshot("existing")
        self.assertEqual(state["preferred_date"], "2026-10-19")
        self.assertEqual(state["revision"], 1)
        self.assertIsNone(state["preferred_time"])

    def test_past_date_correction_revokes_old_confirmation(self):
        old = self.prepare_voice(day="Monday at 10 AM")
        state = self.say("Actually 2026-10-13", automatic=False)
        self.assertIsNone(state["confirmed_proposal"])
        self.assertIsNone(state["proposal_id"])
        self.assertIn("future date", state["message"])
        with self.assertRaises(BookingError):
            self.store.create_booking(self.session, old["proposal_id"])
