import json
import tempfile
import threading
import unittest
from datetime import datetime
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from booking import LOCAL, Store
from server import handler_for


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        store = Store(Path(self.temp.name) / "clinic.sqlite",
                      now=lambda: datetime(2026, 10, 14, 12, tzinfo=LOCAL))
        handler = handler_for(store)
        handler.log_message = lambda *args: None
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:" + str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def post(self, path, data, origin=None):
        headers = {"Content-Type": "application/json"}
        if origin:
            headers["Origin"] = origin
        with urlopen(Request(self.base + path, data=json.dumps(data).encode(), headers=headers)) as response:
            return json.load(response)

    def test_complete_booking_journey_over_http(self):
        state = self.post("/api/session", {})
        session = state["session_id"]
        friday = self.post("/api/chat", {"session_id": session, "sequence": 1, "turn_id": "one", "text": "Friday"})
        monday = self.post("/api/chat", {"session_id": session, "sequence": 2, "turn_id": "two", "text": "Actually Monday"})
        self.assertEqual(monday["booking_count"], 0)
        self.assertNotEqual(friday["proposal_id"], monday["proposal_id"])
        payload = {"session_id": session, "sequence": 3, "turn_id": "three", "text": "yes",
                   "proposal_id": monday["proposal_id"]}
        first = self.post("/api/chat", payload)
        repeated = self.post("/api/chat", payload)
        self.assertEqual(first["booking"]["id"], repeated["booking"]["id"])
        self.assertEqual(repeated["booking_count"], 1)
        with urlopen(self.base + "/api/session?id=" + session) as response:
            restored = json.load(response)
        self.assertEqual(restored["booking"]["id"], first["booking"]["id"])

    def test_direct_booking_endpoint_rejects_missing_confirmation(self):
        state = self.post("/api/session", {})
        offer = self.post("/api/chat", {"session_id": state["session_id"], "text": "Monday", "turn_id": "one", "sequence": 1})
        with self.assertRaises(HTTPError) as raised:
            self.post("/api/book", {"session_id": state["session_id"], "proposal_id": offer["proposal_id"]})
        self.assertEqual(raised.exception.code, 409)

    def test_page_and_client_are_served(self):
        for path, marker in [("/", "Book a dermatologist"), ("/app.js", "confirmation"), ("/style.css", ".layout")]:
            with urlopen(self.base + path) as response:
                text = response.read().decode()
            self.assertIn(marker if path != "/app.js" else "/api/chat", text)

    def test_cross_origin_write_is_rejected(self):
        with self.assertRaises(HTTPError) as raised:
            self.post("/api/session", {}, origin="https://other.example")
        self.assertEqual(raised.exception.code, 403)
