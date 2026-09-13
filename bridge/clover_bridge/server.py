"""The bridge process: adapter <-> Session <-> GUI over WebSocket.

    python -m clover_bridge.server --adapter stub --port 8765
    python -m clover_bridge.server --adapter yourmodule:YourAdapter --port 8765

Then open the GUI with  ?ws=ws://localhost:8765  appended to its URL.
Needs `websockets` (see requirements.txt). Everything else is stdlib.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import logging
from typing import Any

from .adapter import VentAdapter
from .session import Session

log = logging.getLogger("clover.bridge")


def load_adapter(spec: str) -> VentAdapter:
    if spec == "stub":
        from .stub_adapter import StubVent
        return StubVent()
    mod, _, cls = spec.partition(":")
    return getattr(importlib.import_module(mod), cls or "Adapter")()


class Bridge:
    def __init__(self, adapter: VentAdapter, tick: float = 1.0):
        self.adapter = adapter
        self.session = Session()
        self.tick = tick
        self.clients: set[Any] = set()
        self._write_lock = asyncio.Lock()

    # ---- broadcast ----
    async def send_all(self, msg: dict) -> None:
        if not self.clients:
            return
        data = json.dumps(msg, default=lambda o: None)
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)

    async def flush(self) -> None:
        for e in self.session.drain_log():
            await self.send_all({"type": "log", "entry": e})
        await self.send_all(self.session.frame_json())

    # ---- adapter side ----
    async def run_frames(self) -> None:
        async for fr in self.adapter.frames():
            self.session.on_frame(fr)

    async def run_waves(self) -> None:
        async for w in self.adapter.waves():
            await self.send_all(self.session.on_wave(w.key, w.rate, w.samples))

    async def write(self, k: str, val: float, src: str, kind: str) -> None:
        async with self._write_lock:
            self.session.cmd = {"status": "pending", "what": f"{k} {val}", "t": self.session.t}
            res = await self.adapter.set_setting(k, val)
            self.session.on_command_result(k, val, src, kind, res)

    async def apply_decisions(self, decisions) -> None:
        for d in decisions:
            reasoning = d.reasoning
            for c in d.changes:
                if self.session.mode == "AUTO" and self.session.engaged:
                    await self.write(c.key, c.to, "CTRL", reasoning)

    async def run_ticks(self) -> None:
        while True:
            await asyncio.sleep(self.tick)
            decisions = self.session.tick(self.tick)
            await self.apply_decisions(decisions)
            await self.flush()

    # ---- GUI side ----
    async def handle_client(self, ws) -> None:
        self.clients.add(ws)
        try:
            await ws.send(json.dumps(self.session.snapshot_json(), default=lambda o: None))
            async for raw in ws:
                try:
                    cmd = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if cmd.get("type") == "scenario" and hasattr(self.adapter, "scenario"):
                    self.adapter.scenario(cmd.get("name", ""))   # demo scaffolding only
                    self.session.add_log("SYS", f"[sim] {cmd.get('name')}", "")
                elif cmd.get("type") == "cmd":
                    writes = self.session.handle_command(cmd)
                    for k, val, src, kind in writes:
                        await self.write(k, val, src, kind)
                await self.flush()
        finally:
            self.clients.discard(ws)

    async def run(self, host: str, port: int) -> None:
        import websockets  # lazy: only the network layer needs it
        self.session.identity = await self.adapter.start()
        self.session.add_log("SYS", "CLOVER bridge started",
                             f"{self.session.identity.make} {self.session.identity.model} via {self.session.identity.transport}.")
        async with websockets.serve(self.handle_client, host, port):
            log.info("bridge listening on ws://%s:%d", host, port)
            await asyncio.gather(self.run_frames(), self.run_waves(), self.run_ticks())


def main() -> None:
    ap = argparse.ArgumentParser(description="CLOVER GUI <-> ventilator bridge")
    ap.add_argument("--adapter", default="stub", help="'stub' or 'module.path:ClassName'")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(Bridge(load_adapter(a.adapter)).run(a.host, a.port))


if __name__ == "__main__":
    main()
