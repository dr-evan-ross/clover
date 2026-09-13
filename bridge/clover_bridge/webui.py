"""Tiny static web server so nobody has to type file paths or ?ws= parameters.

Serves:
  /            the Pulse control page (bridge/control.html)
  /clover      the CLOVER medic display (gui/medic-console-prototype.html)
  /config.json {"ws": "ws://<host>:<ws_port>"} so both pages find the bridge

Standard library only; runs in a background thread next to the asyncio bridge.
"""
from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", ".."))
PAGES = {
    "/": os.path.join(REPO, "bridge", "control.html"),
    "/clover": os.path.join(REPO, "gui", "medic-console-prototype.html"),
}


def make_handler(ws_port: int):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/config.json":
                host = self.headers.get("Host", "localhost").split(":")[0]
                body = json.dumps({"ws": f"ws://{host}:{ws_port}"}).encode()
                return self._send(200, "application/json", body)
            if path == "/favicon.ico":
                return self._send(204, "text/plain", b"")
            f = PAGES.get(path)
            if not f or not os.path.exists(f):
                return self._send(404, "text/plain", b"not found")
            with open(f, "rb") as fh:
                return self._send(200, "text/html; charset=utf-8", fh.read())

        def _send(self, code, ctype, body):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if body:
                self.wfile.write(body)
    return H


def serve_in_thread(host: str, http_port: int, ws_port: int) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer((host, http_port), make_handler(ws_port))
    t = threading.Thread(target=srv.serve_forever, daemon=True, name="clover-webui")
    t.start()
    return srv
