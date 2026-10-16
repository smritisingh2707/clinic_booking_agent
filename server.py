import argparse
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from booking import BookingError, Store
from voice_gateway import VoiceGateway

WEB = Path(__file__).parent / "web"


def handler_for(store, voice_gateway=None):
    gateway = voice_gateway or VoiceGateway()
    class Handler(BaseHTTPRequestHandler):
        def json_response(self, status, data):
            payload = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            url = urlparse(self.path)
            try:
                if url.path == "/api/health":
                    return self.json_response(200, {"status": "ok", "mode": "mock-clinic"})
                if url.path == "/api/voice/config":
                    return self.json_response(200, gateway.config())
                if url.path == "/api/session":
                    session_id = parse_qs(url.query).get("id", [""])[0]
                    return self.json_response(200, store.snapshot(session_id))
                target = (WEB / (url.path.lstrip("/") or "index.html")).resolve()
                if not target.is_relative_to(WEB.resolve()) or not target.is_file():
                    return self.json_response(404, {"error": "Not found."})
                payload = target.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(payload)
            except BookingError as exc:
                self.json_response(exc.status, {"error": str(exc)})

        def do_POST(self):
            try:
                origin = self.headers.get("Origin")
                if origin and origin != "http://" + self.headers.get("Host", ""):
                    raise BookingError("Use the local demo origin.", 403)
                if not self.headers.get("Content-Type", "").startswith("application/json"):
                    raise BookingError("Send application/json.", 415)
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 16000:
                    raise BookingError("Invalid request size.", 400)
                args = json.loads(self.rfile.read(length))
                if not isinstance(args, dict):
                    raise BookingError("JSON object required.", 400)
                path = urlparse(self.path).path
                if path == "/api/session":
                    if args.get("voice"):
                        gateway.authorize_agent(self.headers.get("X-Voice-Agent-Key"))
                    result = store.new_session(voice=bool(args.get("voice")))
                elif path == "/api/voice/connect":
                    result = gateway.connect(store, args.get("session_id"))
                elif path == "/api/voice/played":
                    gateway.authorize_agent(self.headers.get("X-Voice-Agent-Key"))
                    result = store.mark_played(args["session_id"], args["proposal_id"], args["revision"])
                elif path in {"/api/chat", "/api/observe"}:
                    if store.snapshot(args["session_id"])["voice_mode"]:
                        if path == "/api/chat":
                            raise BookingError("This is a voice session. Start a new session for text mode.")
                        gateway.authorize_agent(self.headers.get("X-Voice-Agent-Key"))
                    result = store.observe(
                        args["session_id"], args["text"], args["turn_id"],
                        args["sequence"], automatic=path == "/api/chat",
                        confirmation_proposal=args.get("proposal_id"),
                    )
                elif path == "/api/availability":
                    result = store.availability(args["session_id"], args["revision"])
                elif path == "/api/propose":
                    result = store.propose(args["session_id"], args["slot_id"], args["revision"])
                elif path == "/api/book":
                    result = store.create_booking(args["session_id"], args["proposal_id"], args.get("operation_key"))
                else:
                    raise BookingError("Unknown endpoint.", 404)
                self.json_response(200, result)
            except BookingError as exc:
                self.json_response(exc.status, {"error": str(exc)})
            except (KeyError, ValueError, TypeError) as exc:
                self.json_response(400, {"error": "Invalid request: " + str(exc)})

    return Handler


def main():
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).parent / ".env")
    except ImportError:
        pass
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--db", default="data/clinic.sqlite")
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), handler_for(Store(args.db)))
    print(f"Open http://{args.host}:{args.port} — local mock clinic", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
