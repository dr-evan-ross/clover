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


def load_adapter(spec: str, **opts) -> VentAdapter:
    if spec == "stub":
        from .stub_adapter import StubVent
        return StubVent(speed=opts.get("speed", 1.0))
    if spec == "pulse":
        from .pulse_adapter import PulseVent
        return PulseVent(patient=opts.get("patient", "DefaultMale"), root=opts.get("root"),
                         speed=opts.get("speed", 1.0))
    mod, _, cls = spec.partition(":")
    return getattr(importlib.import_module(mod), cls or "Adapter")()


class Bridge:
    """Session time runs in *simulation* seconds. With ``speed`` > 1 (accelerated demos)
    the session ticks ``speed`` times per wall second so the controller cycle, override
    timers and stale detection stay in step with the physiology; GUI frames are throttled
    to ``gui_hz`` so the browser is not flooded."""

    def __init__(self, adapter: VentAdapter, tick: float = 1.0, speed: float = 1.0, gui_hz: float = 4.0):
        self.adapter = adapter
        self.session = Session()
        self.tick = tick
        self.speed = max(0.01, float(speed))
        self.gui_min_interval = 1.0 / gui_hz
        self._last_flush = 0.0
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

    async def flush(self, force: bool = False) -> None:
        logs = self.session.drain_log()
        for e in logs:
            await self.send_all({"type": "log", "entry": e})
        now = asyncio.get_event_loop().time()
        if force or logs or now - self._last_flush >= self.gui_min_interval:
            self._last_flush = now
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
            await asyncio.sleep(self.tick / self.speed)
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
                    try:
                        self.adapter.scenario(cmd.get("name", ""), cmd.get("seconds"))   # demo scaffolding only
                        self.session.add_log("SYS", f"[sim] {cmd.get('name')}", "")
                    except Exception as e:  # unknown scenario for this adapter, or engine refused
                        self.session.add_log("SYS", f"[sim] {cmd.get('name')} not available", f"{type(e).__name__}: {e}")
                elif cmd.get("type") == "cmd":
                    writes = self.session.handle_command(cmd)
                    for k, val, src, kind in writes:
                        await self.write(k, val, src, kind)
                await self.flush(force=True)
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
    ap.add_argument("--adapter", default="stub", help="'stub', 'pulse', or 'module.path:ClassName'")
    ap.add_argument("--patient", default="DefaultMale", help="pulse: patient state name (e.g. CSTARS-Patient3) or path")
    ap.add_argument("--speed", type=float, default=1.0, help="stub/pulse: simulation speed multiple of real time")
    ap.add_argument("--vent-optimizer", dest="root", default=None, help="pulse: path to the vent_optimizer project (default ../vent_optimizer)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(Bridge(load_adapter(a.adapter, patient=a.patient, speed=a.speed, root=a.root), speed=a.speed).run(a.host, a.port))


if __name__ == "__main__":
    main()
