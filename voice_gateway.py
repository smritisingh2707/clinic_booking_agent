"""Local token endpoint. LiveKit secrets never go to the browser."""
import hashlib
import hmac
import json
import os
import uuid
from datetime import timedelta

from booking import BookingError


def agent_key(secret):
    return hmac.new(secret.encode(), b"clinic-booking-agent-api", hashlib.sha256).hexdigest()


class VoiceGateway:
    def __init__(self, url=None, key=None, secret=None):
        self.url = url if url is not None else os.environ.get("LIVEKIT_URL", "")
        self.key = key if key is not None else os.environ.get("LIVEKIT_API_KEY", "")
        self.secret = secret if secret is not None else os.environ.get("LIVEKIT_API_SECRET", "")

    def config(self):
        configured = all(value and not value.startswith("replace-") for value in
                         [self.url, self.key, self.secret]) and "your-project" not in self.url
        return {"configured": configured, "agent_name": "clinic-booking",
                "message": "Ready to connect to LiveKit." if configured else
                "Add LiveKit credentials to .env and start the server with the voice extra."}

    def authorize_agent(self, header):
        if not self.secret or not hmac.compare_digest(header or "", agent_key(self.secret)):
            raise BookingError("Only the voice agent can report speech or record voice turns.", 403)

    def connect(self, store, session_id=None):
        if not self.config()["configured"]:
            raise BookingError(self.config()["message"], 503)
        try:
            from livekit import api
        except ImportError:
            raise BookingError("Install the voice extra: uv sync --extra voice", 503)
        state = store.enable_voice(session_id) if session_id else store.new_session(voice=True)
        room = "clinic-" + uuid.uuid4().hex
        token = (api.AccessToken(self.key, self.secret)
                 .with_identity("patient-" + uuid.uuid4().hex)
                 .with_name("Demo patient")
                 .with_ttl(timedelta(minutes=5))
                 .with_grants(api.VideoGrants(room_join=True, room=room,
                    can_publish=True, can_publish_sources=["microphone"],
                    can_subscribe=True, can_publish_data=False))
                 .with_room_config(api.RoomConfiguration(agents=[api.RoomAgentDispatch(
                    agent_name="clinic-booking", metadata=json.dumps({"session_id": state["session_id"]}))]))
                 .to_jwt())
        return {"server_url": self.url, "participant_token": token,
                "room_name": room, "state": state}
