"""Run the exact Friday -> Monday -> confirm scenario without keys or audio."""
import json
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

from booking import LOCAL, Store

with tempfile.TemporaryDirectory() as directory:
    service = Store(Path(directory) / "demo.sqlite",
                    now=lambda: datetime(2026, 10, 14, 12, tzinfo=LOCAL))
    session = service.new_session()["session_id"]
    for sequence, text in enumerate(["Friday", "Actually, make that Monday", "yes"], start=1):
        previous = service.snapshot(session)
        result = service.observe(session, text, str(uuid.uuid4()), sequence, automatic=True,
                                 confirmation_proposal=previous["proposal_id"])
        print("PATIENT:", text)
        print("AGENT:", result["message"])
        print("BOOKINGS:", result["booking_count"])
    print(json.dumps(result["booking"], indent=2))
