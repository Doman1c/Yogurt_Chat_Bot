"""Loopback-only Swift integration fixture; not a production Gateway."""
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass
    def authorized(self):
        if self.headers.get("Authorization") != "Bearer TEST_ONLY_NOT_A_REAL_CREDENTIAL_000000":
            self.send_response(401); self.end_headers(); return False
        return True
    def json(self, body, status=200):
        raw = json.dumps(body).encode()
        self.send_response(status); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def do_POST(self):
        if not self.authorized(): return
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        assert set(body) <= {"client_message_id", "text", "surface_type", "conversation_id", "device_instance_id"}
        self.json(dict(conversation_id="c1", turn_ref="t1", response_id="r1", event_stream_url="/v1/native/responses/r1/events", admission="NEWLY_ADMITTED"), 202)
    def do_GET(self):
        if not self.authorized(): return
        errors = {
            "/v1/native/responses/unauthorized/events": {"code": "UNAUTHORIZED"},
            "/v1/native/responses/rawerror/events": {"code": "TEST_ONLY_NOT_A_REAL_CREDENTIAL_000000"},
            "/v1/native/responses/oversize/events": {"code": "UNAUTHORIZED", "padding": "x" * 5000},
        }
        if self.path in errors:
            self.json(errors[self.path], 401); return
        if self.path == "/v1/native/health":
            self.json(dict(status="ok", model_token_streaming=True)); return
        if self.path == "/v1/native/conversations":
            self.json([]); return
        if self.path != "/v1/native/responses/r1/events":
            self.json(dict(code="NOT_FOUND"), 404); return
        after = int(self.headers.get("Last-Event-ID", "0"))
        self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
        events = [("response.started", None), ("response.delta", " live "), ("response.delta", "🙂\n  "), ("response.completed", None)]
        for n, (kind, text) in enumerate(events, 1):
            if n <= after: continue
            data = dict(response_id="r1", sequence=n, event=kind, text=text, reason=None)
            frame = ("id: %s\nevent: %s\ndata: %s\n\n" % (n, kind, json.dumps(data, ensure_ascii=False))).encode()
            for byte in frame:
                self.wfile.write(bytes([byte])); self.wfile.flush()
            time.sleep(0.05)

ThreadingHTTPServer(("127.0.0.1", 17622), Handler).serve_forever()
