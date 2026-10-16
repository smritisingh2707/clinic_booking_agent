"""The booking contract. This module has no LLM or speech dependencies."""
import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

LOCAL = ZoneInfo("Asia/Kolkata")
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
YES = {"yes", "yes please", "confirm", "confirm booking", "book it", "yes book it", "yes confirm"}
HOUR_WORDS = dict(zip(
    "one two three four five six seven eight nine ten eleven twelve".split(), range(1, 13)))
CORRECTION = r"\b(actually|instead|change|different|rather|make (?:it|that))\b"


class BookingError(Exception):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


def normalize(text):
    return re.sub(r"[^a-z0-9 ]", " ", text.lower()).strip()


def preferred_day(text, today):
    """Use the last supported date mention, including self-corrections."""
    mentions = []
    for match in re.finditer(r"\b(20\d{2}-\d{2}-\d{2})\b", text):
        try:
            mentions.append((match.start(), date.fromisoformat(match.group())))
        except ValueError:
            raise BookingError("Please give a valid date.", 400)
    for match in re.finditer(r"\b(" + "|".join(DAYS) + r")\b", text.lower()):
        delta = (DAYS.index(match.group()) - today.weekday()) % 7
        # This demo interprets a weekday as the next occurrence after today.
        mentions.append((match.start(), today + timedelta(days=delta or 7)))
    return max(mentions, key=lambda x: x[0])[1].isoformat() if mentions else None


def preferred_time(text):
    """Parse exact 12-hour/24-hour times; ask again for unsupported time constraints."""
    source = re.sub(r"\b20\d{2}-\d{2}-\d{2}\b", "", text.lower())
    hours = r"\d{1,2}|" + "|".join(HOUR_WORDS)
    pattern = (r"\b(?P<hour>" + hours + r")"
               r"(?::(?P<minute>\d{2})|\s+(?P<spoken>thirty|fifteen|forty five))?"
               r"\s*(?P<period>[ap]\s*\.?\s*m\.?|in the morning|in the afternoon|in the evening)\b")
    mentions, occupied = [], []
    for match in re.finditer(pattern, source):
        raw = match["hour"]
        hour = HOUR_WORDS[raw] if raw in HOUR_WORDS else int(raw)
        minute = int(match["minute"] or {"thirty": 30, "fifteen": 15, "forty five": 45}.get(match["spoken"], 0))
        if not 1 <= hour <= 12 or minute > 59:
            raise BookingError("Please give a valid time, for example 10 AM or 14:00.", 400)
        period = re.sub(r"[ .]", "", match["period"])
        pm = period.startswith("p") or "afternoon" in period or "evening" in period
        mentions.append((match.start(), f"{hour % 12 + 12 * pm:02d}:{minute:02d}"))
        occupied.append(match.span())
    for match in re.finditer(r"\b(\d{1,2}):(\d{2})\b", source):
        if any(start <= match.start() < end for start, end in occupied):
            continue
        hour, minute = map(int, match.groups())
        if hour > 23 or minute > 59:
            raise BookingError("Please give a valid 24-hour time.", 400)
        mentions.append((match.start(), f"{hour:02d}:{minute:02d}"))
    for match in re.finditer(r"\b(noon|midnight)\b", source):
        mentions.append((match.start(), "12:00" if match[1] == "noon" else "00:00"))
    if mentions:
        if re.search(r"\b(before|after|around|between|until|from)\b", source):
            raise BookingError("Please choose one exact time, for example 10 AM.", 400)
        return max(mentions, key=lambda item: item[0])[1]
    if re.search(r"\bat\s+(?:" + hours + r")\b|\b(morning|afternoon|evening|half past|quarter|o.clock)\b", source):
        raise BookingError("Please include AM or PM, or use a 24-hour time such as 14:00.", 400)
    return None


def asks_about_offer(text):
    """Recognize a small set of clarification forms without treating requests as questions."""
    clean = " ".join(normalize(text).split())
    return not re.search(CORRECTION, clean) and bool(re.match(
        r"^(?:is (?:that|it|the appointment|the offer)|did you say|"
        r"(?:what|which) (?:time|day|date|doctor)|who is the doctor|"
        r"can you (?:repeat|remind me)|please repeat)\b", clean))


class Store:
    def __init__(self, path, now=None):
        self.path = str(path)
        self.now = now or (lambda: datetime.now(LOCAL))
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS slots(
              id TEXT PRIMARY KEY, doctor TEXT NOT NULL, starts_at TEXT NOT NULL UNIQUE
            );
            CREATE TABLE IF NOT EXISTS sessions(
              id TEXT PRIMARY KEY, preferred_date TEXT, preferred_time TEXT,
              revision INTEGER NOT NULL DEFAULT 0,
              last_sequence INTEGER NOT NULL DEFAULT 0, proposal_id TEXT, slot_id TEXT,
              confirmed_proposal TEXT, voice_mode INTEGER NOT NULL DEFAULT 0,
              played_proposal TEXT
            );
            CREATE TABLE IF NOT EXISTS bookings(
              id TEXT PRIMARY KEY, session_id TEXT NOT NULL UNIQUE,
              proposal_id TEXT NOT NULL, slot_id TEXT NOT NULL UNIQUE,
              operation_key TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL,
              FOREIGN KEY(slot_id) REFERENCES slots(id)
            );
            CREATE TABLE IF NOT EXISTS turns(
              session_id TEXT NOT NULL, turn_id TEXT NOT NULL, text TEXT NOT NULL,
              request_json TEXT NOT NULL, response TEXT NOT NULL, PRIMARY KEY(session_id, turn_id)
            );
            CREATE TABLE IF NOT EXISTS events(
              id INTEGER PRIMARY KEY, session_id TEXT NOT NULL,
              kind TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL
            );
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(sessions)")}
            for name, declaration in [("voice_mode", "INTEGER NOT NULL DEFAULT 0"),
                                      ("played_proposal", "TEXT"), ("preferred_time", "TEXT")]:
                if name not in columns:
                    db.execute(f"ALTER TABLE sessions ADD COLUMN {name} {declaration}")
            for offset in range(1, 22):
                day = self.now().date() + timedelta(days=offset)
                if day.weekday() >= 5:
                    continue
                for hour, doctor in [(9, "Dr Mira Rao"), (10, "Dr Leena Shah"), (14, "Dr Mira Rao")]:
                    starts = datetime.combine(day, datetime.min.time(), LOCAL).replace(hour=hour).isoformat()
                    db.execute("INSERT OR IGNORE INTO slots VALUES (?,?,?)",
                               (day.isoformat() + "-" + str(hour), doctor, starts))

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def event(self, db, session_id, kind, detail):
        db.execute("INSERT INTO events(session_id,kind,detail,created_at) VALUES(?,?,?,?)",
                   (session_id, kind, json.dumps(detail), self.now().isoformat()))

    def new_session(self, voice=False):
        session_id = str(uuid.uuid4())
        with self.connection() as db:
            db.execute("INSERT INTO sessions(id,voice_mode) VALUES(?,?)", (session_id, int(voice)))
        return self.snapshot(session_id)

    def enable_voice(self, session_id):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            session = self.session(db, session_id)
            if not session["voice_mode"]:
                db.execute("""UPDATE sessions SET voice_mode=1,revision=revision+1,
                  proposal_id=NULL,slot_id=NULL,confirmed_proposal=NULL,played_proposal=NULL
                  WHERE id=?""", (session_id,))
        return self.snapshot(session_id)

    def mark_played(self, session_id, proposal_id, revision):
        """An authenticated agent reports completed, uninterrupted offer playout."""
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            session = self.session(db, session_id)
            if (not session["voice_mode"] or not proposal_id
                    or session["proposal_id"] != proposal_id or session["revision"] != revision):
                raise BookingError("This spoken offer is no longer current.")
            db.execute("UPDATE sessions SET played_proposal=? WHERE id=?", (proposal_id, session_id))
            self.event(db, session_id, "offer_playout_completed", {"proposal_id": proposal_id})
        return self.snapshot(session_id)

    def session(self, db, session_id):
        row = db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        if row is None:
            raise BookingError("Unknown session.", 404)
        return dict(row)

    def booking(self, db, session_id):
        row = db.execute("""
          SELECT b.*,s.doctor,s.starts_at FROM bookings b JOIN slots s ON s.id=b.slot_id
          WHERE b.session_id=?
        """, (session_id,)).fetchone()
        return dict(row) if row else None

    def _available(self, db, session, match_time=True):
        wanted_time = session["preferred_time"] if match_time else None
        return [dict(row) for row in db.execute("""
          SELECT s.* FROM slots s LEFT JOIN bookings b ON b.slot_id=s.id
          WHERE b.id IS NULL AND substr(s.starts_at,1,10)=? AND s.starts_at>?
          AND (? IS NULL OR substr(s.starts_at,12,5)=?)
          ORDER BY s.starts_at
        """, (session["preferred_date"], self.now().isoformat(), wanted_time, wanted_time))]

    def availability(self, session_id, revision):
        with self.connection() as db:
            session = self.session(db, session_id)
            if session["revision"] != revision:
                raise BookingError("The request changed. Read the current session and search again.")
            slots = self._available(db, session)
            return {"revision": revision, "preferred_date": session["preferred_date"],
                    "preferred_time": session["preferred_time"], "slots": slots,
                    "alternatives": self._available(db, session, match_time=False) if not slots else [],
                    "instruction": "An alternative time requires a new patient choice before it can be offered."}

    def _propose(self, db, session_id, slot_id, revision):
        session = self.session(db, session_id)
        if self.booking(db, session_id):
            raise BookingError("This session already has a booking. Rescheduling is a separate operation.")
        if revision != session["revision"]:
            raise BookingError("Stale availability result. The patient changed their request.")
        if slot_id not in {row["id"] for row in self._available(db, session)}:
            raise BookingError("That slot is no longer available for the current request.")
        if session["proposal_id"] and session["slot_id"] == slot_id:
            return session["proposal_id"]
        proposal_id = str(uuid.uuid4())
        db.execute("UPDATE sessions SET proposal_id=?,slot_id=?,confirmed_proposal=NULL,played_proposal=NULL WHERE id=?",
                   (proposal_id, slot_id, session_id))
        self.event(db, session_id, "offer_prepared", {"proposal_id": proposal_id, "slot_id": slot_id, "revision": revision})
        return proposal_id

    def propose(self, session_id, slot_id, revision):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            proposal = self._propose(db, session_id, slot_id, revision)
        return {"proposal_id": proposal, **self.snapshot(session_id)}

    def _create(self, db, session_id, proposal_id, operation_key):
        existing_key = db.execute("SELECT * FROM bookings WHERE operation_key=?", (operation_key,)).fetchone()
        if existing_key:
            if existing_key["session_id"] != session_id or existing_key["proposal_id"] != proposal_id:
                raise BookingError("An idempotency key cannot be reused for a different request.")
            return self.booking(db, session_id)
        existing = self.booking(db, session_id)
        if existing:
            if existing["proposal_id"] == proposal_id:
                return existing
            raise BookingError("A booking already exists. A new date needs an explicit rescheduling flow.")
        session = self.session(db, session_id)
        if proposal_id != session["proposal_id"] or proposal_id != session["confirmed_proposal"]:
            raise BookingError("The current offer needs explicit patient confirmation.")
        if session["voice_mode"] and session["played_proposal"] != proposal_id:
            raise BookingError("The current voice offer has not finished playing.")
        slots = {row["id"]: row for row in self._available(db, session)}
        if session["slot_id"] not in slots:
            raise BookingError("The slot was taken. Check availability and offer another time.")
        booking_id = "BK-" + uuid.uuid4().hex[:10].upper()
        db.execute("INSERT INTO bookings VALUES(?,?,?,?,?,?)",
                   (booking_id, session_id, proposal_id, session["slot_id"], operation_key, self.now().isoformat()))
        self.event(db, session_id, "booking_created", {"booking_id": booking_id, "slot_id": session["slot_id"]})
        return self.booking(db, session_id)

    def create_booking(self, session_id, proposal_id, operation_key=None):
        # The same proposal gets the same key on every retry.
        operation_key = operation_key or ("booking:" + proposal_id)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            result = self._create(db, session_id, proposal_id, operation_key)
        return result

    def observe(self, session_id, text, turn_id, sequence, automatic=False, confirmation_proposal=None):
        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            raise BookingError("Give a non-empty utterance of at most 2,000 characters.", 400)
        if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
            raise BookingError("A positive turn sequence is required.", 400)
        request_json = json.dumps({
            "text": text, "sequence": sequence, "automatic": bool(automatic),
            "proposal_id": confirmation_proposal,
        }, sort_keys=True)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            session = self.session(db, session_id)
            duplicate = db.execute("SELECT * FROM turns WHERE session_id=? AND turn_id=?", (session_id, turn_id)).fetchone()
            if duplicate:
                if duplicate["request_json"] != request_json:
                    raise BookingError("A turn ID cannot be reused with a different request.")
                return json.loads(duplicate["response"])
            if sequence <= session["last_sequence"]:
                raise BookingError("This utterance arrived after a newer turn and was ignored.")
            db.execute("UPDATE sessions SET last_sequence=? WHERE id=?", (sequence, session_id))
            existing = self.booking(db, session_id)
            parse_error = None
            clarification = bool(session["proposal_id"]) and asks_about_offer(text)
            try:
                requested = None if clarification else preferred_day(text, self.now().date())
                time_requested = None if clarification else preferred_time(text)
                if requested and requested < self.now().date().isoformat():
                    raise BookingError("Please choose a future date.", 400)
            except BookingError as exc:
                requested, time_requested, parse_error = None, None, str(exc)
            clean = " ".join(normalize(text).split())
            any_time = bool(re.search(r"\bany\s*time\b", clean)) and not clarification
            request_changed = bool(requested or time_requested or any_time)
            ambiguous = request_changed and re.search(r"\b(not|unless|except|either|or|instead of)\b|\bdon.t\b", text.lower())
            unclear_correction = re.search(CORRECTION, clean) and not request_changed
            self.event(db, session_id, "user_turn", {"text": text, "sequence": sequence})
            message = ""
            if existing:
                message = "You already have one booking. A correction now requires an explicit rescheduling operation."
            elif clarification:
                # A question is not fresh consent, but the unchanged spoken offer stays valid.
                db.execute("UPDATE sessions SET confirmed_proposal=NULL WHERE id=?", (session_id,))
                slot = db.execute("SELECT * FROM slots WHERE id=?", (session["slot_id"],)).fetchone()
                stamp = datetime.fromisoformat(slot["starts_at"])
                message = (f"The current offer is {slot['doctor']} on "
                           f"{stamp.strftime('%A, %d %B at %I:%M %p')} IST. Nothing has been booked.")
                self.event(db, session_id, "offer_clarified", {"proposal_id": session["proposal_id"]})
            elif parse_error or ambiguous or unclear_correction:
                db.execute("""
                  UPDATE sessions SET preferred_date=NULL,preferred_time=NULL,revision=revision+1,
                  proposal_id=NULL,slot_id=NULL,confirmed_proposal=NULL,played_proposal=NULL WHERE id=?
                """, (session_id,))
                request_changed = False
                message = "I haven’t booked anything. " + (parse_error or "Please restate the preferred weekday or a valid YYYY-MM-DD date.")
            elif request_changed:
                requested = requested or session["preferred_date"]
                wanted_time = None if any_time else time_requested or session["preferred_time"]
                if requested is None:
                    message = "Which weekday or YYYY-MM-DD date would you like at that time?"
                    request_changed = False
                db.execute("""
                  UPDATE sessions SET preferred_date=?,preferred_time=?,revision=revision+1,
                  proposal_id=NULL,slot_id=NULL,confirmed_proposal=NULL,played_proposal=NULL WHERE id=?
                """, (requested, wanted_time, session_id))
                self.event(db, session_id, "intent_revised", {"preferred_date": requested, "preferred_time": wanted_time})
                if requested:
                    message = "I’ll check dermatologist availability for " + requested + (" at " + wanted_time + " IST" if wanted_time else "") + "."
            elif re.match(r"^(no|stop|cancel|wait)\b", clean):
                db.execute("""
                  UPDATE sessions SET preferred_date=NULL,preferred_time=NULL,revision=revision+1,
                  proposal_id=NULL,slot_id=NULL,confirmed_proposal=NULL,played_proposal=NULL WHERE id=?
                """, (session_id,))
                message = "I haven’t booked anything. Which day would you prefer?"
            elif clean in YES and session["proposal_id"]:
                if confirmation_proposal != session["proposal_id"]:
                    raise BookingError("The offer changed. Confirm the currently displayed or spoken offer.")
                if session["voice_mode"] and session["played_proposal"] != session["proposal_id"]:
                    raise BookingError("Please let the current offer finish, then confirm it.")
                db.execute("UPDATE sessions SET confirmed_proposal=proposal_id WHERE id=?", (session_id,))
                self.event(db, session_id, "patient_confirmed", {"proposal_id": session["proposal_id"]})
                if automatic:
                    self._create(db, session_id, session["proposal_id"], "booking:" + session["proposal_id"])
                message = "Your current choice is confirmed."
            else:
                db.execute("UPDATE sessions SET confirmed_proposal=NULL WHERE id=?", (session_id,))
                message = "Say a weekday or a date such as 2026-10-19. After I offer a slot, say ‘yes’ or ‘book it’ to confirm."
            session = self.session(db, session_id)
            if automatic and request_changed and not existing:
                available = self._available(db, session)
                if available:
                    self._propose(db, session_id, available[0]["id"], session["revision"])
                    slot = available[0]
                    stamp = datetime.fromisoformat(slot["starts_at"])
                    message = f"{slot['doctor']} has {stamp.strftime('%A, %d %B at %I:%M %p')} IST available. Shall I book that?"
                else:
                    message = "No mock dermatologist slots match that date and time."
                    alternatives = self._available(db, session, match_time=False)
                    if alternatives:
                        times = ", ".join(datetime.fromisoformat(slot["starts_at"]).strftime("%I:%M %p") for slot in alternatives)
                        message += " Available times that day are " + times + " IST. Which time would you like?"
                    else:
                        message += " Please choose another weekday."
            snapshot = self._snapshot(db, session_id)
            if snapshot["booking"] and not existing:
                item = snapshot["booking"]
                stamp = datetime.fromisoformat(item["starts_at"])
                message = f"Booked with {item['doctor']} on {stamp.strftime('%A, %d %B at %I:%M %p')} IST. Your reference is {item['id']}."
            result = {"message": message, **snapshot}
            db.execute("INSERT INTO turns VALUES(?,?,?,?,?)",
                       (session_id, turn_id, text, request_json, json.dumps(result)))
        return result

    def _snapshot(self, db, session_id):
        session = self.session(db, session_id)
        slot = db.execute("SELECT * FROM slots WHERE id=?", (session["slot_id"],)).fetchone()
        booking = self.booking(db, session_id)
        return {
            "session_id": session_id, "revision": session["revision"],
            "last_sequence": session["last_sequence"], "preferred_date": session["preferred_date"],
            "preferred_time": session["preferred_time"],
            "proposal_id": session["proposal_id"], "confirmed_proposal": session["confirmed_proposal"],
            "voice_mode": bool(session["voice_mode"]), "played_proposal": session["played_proposal"],
            "offer": dict(slot) if slot else None, "booking": booking,
            "booking_count": db.execute("SELECT count(*) FROM bookings WHERE session_id=?", (session_id,)).fetchone()[0],
            "events": [dict(row) for row in db.execute("SELECT * FROM events WHERE session_id=? ORDER BY id", (session_id,))],
        }

    def snapshot(self, session_id):
        with self.connection() as db:
            return self._snapshot(db, session_id)
