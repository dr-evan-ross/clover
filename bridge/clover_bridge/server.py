"""The bridge process: adapter <-> Session <-> GUI over WebSocket, plus a small web UI.

    python3 start_clover.py                       # easiest: serves both pages, opens the browser
    python3 -m clover_bridge.server --adapter stub # start with the stub already running
    python3 -m clover_bridge.server --adapter pulse --patient CSTARS-Patient3 --speed 5

Pages (served on --http-port, default 8765):
    http://localhost:8765/         Pulse control page: pick a patient, start/stop, inject scenarios
    http://localhost:8765/clover   the CLOVER medic display, already pointed at this bridge
The WebSocket itself listens on --ws-port (default 8766).

Sessions can be started, stopped and restarted from the control page; the CLOVER
display simply shows LINK LOST while no session is running.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import logging
import time
import webbrowser
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
                         speed=opts.get("speed", 1.0), init_settings=opts.get("init_settings"))
    mod, _, cls = spec.partition(":")
    return getattr(importlib.import_module(mod), cls or "Adapter")()


class Bridge:
    """Session time runs in *simulation* seconds. With ``speed`` > 1 (accelerated demos)
    the session ticks ``speed`` times per wall second so the controller cycle, override
    timers and stale detection stay in step with the physiology; GUI frames are throttled
    to ``gui_hz`` so the browser is not flooded."""

    def __init__(self, adapter: VentAdapter | None = None, tick: float = 1.0, speed: float = 1.0,
                 gui_hz: float = 4.0, root: str | None = None):
        self.adapter = adapter
        self.session = Session()
        self.tick = tick
        self.speed = max(0.01, float(speed))
        self.gui_min_interval = 1.0 / gui_hz
        self._last_flush = 0.0
        self.clients: set[Any] = set()
        self._write_lock = asyncio.Lock()
        self._tasks: list[asyncio.Task] = []
        self.root = root
        self.kind = None
        self.opts: dict = {}
        self.achieved = 0.0
        self._sp_t = self._sp_w = None
        self.scenario_log: list[dict] = []

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

    # ---- sessions ----
    def status(self) -> dict:
        s = self.session
        return {
            "type": "admin_status", "running": self.adapter is not None, "kind": self.kind, "opts": self.opts,
            "speed": self.speed, "achieved": round(self.achieved, 1), "t": round(s.t, 1),
            "identity": None if s.identity is None else {"make": s.identity.make, "model": s.identity.model, "serial": s.identity.serial},
            "mode": s.mode, "engaged": s.engaged, "spo2": s.shown.get("spo2"), "etco2": s.shown.get("etco2"),
            "settings": s.settings, "link": s.link_json(), "scenarios": self.scenario_log[-50:],
            "patients": self.patients(),
        }

    def patients(self) -> list[str]:
        try:
            from .pulse_adapter import PulseVent
            return PulseVent.list_patients(self.root)
        except Exception:
            return []

    async def start_session(self, kind: str, **opts) -> None:
        await self.stop_session()
        self.kind, self.opts = kind, opts
        self.speed = max(0.01, float(opts.get("speed", 1.0)))
        self.session = Session()
        self.scenario_log = []
        adapter = load_adapter(kind, root=self.root, **opts)
        self.session.identity = await adapter.start()
        self.adapter = adapter
        self.session.add_log("SYS", "CLOVER bridge session started",
                             f"{self.session.identity.make} {self.session.identity.model} via {self.session.identity.transport}; speed {self.speed:g}x.")
        self._tasks = [asyncio.create_task(self.run_frames()), asyncio.create_task(self.run_waves())]
        self._sp_t = self._sp_w = None
        await self.flush(force=True)
        await self.send_all(self.status())

    async def stop_session(self) -> None:
        for t in self._tasks:
            t.cancel()
        self._tasks = []
        if self.adapter is not None:
            try:
                await self.adapter.stop()
            except Exception:
                pass
            self.session.add_log("SYS", "CLOVER bridge session stopped", "")
            self.adapter = None
            self.session.connected = False
        await self.flush(force=True)
        await self.send_all(self.status())

    # ---- adapter side ----
    async def run_frames(self) -> None:
        async for fr in self.adapter.frames():
            self.session.on_frame(fr)

    async def run_waves(self) -> None:
        async for w in self.adapter.waves():
            await self.send_all(self.session.on_wave(w.key, w.rate, w.samples))

    async def write(self, k: str, val: float, src: str, kind: str) -> None:
        if self.adapter is None:
            return
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
            if self.adapter is None:
                continue
            decisions = self.session.tick(self.tick)
            await self.apply_decisions(decisions)
            now = time.monotonic()
            if self._sp_t is None or now - self._sp_w >= 2.0:
                if self._sp_t is not None:
                    self.achieved = (self.session.t - self._sp_t) / (now - self._sp_w)
                self._sp_t, self._sp_w = self.session.t, now
            await self.flush()

    # ---- GUI side ----
    async def handle_client(self, ws) -> None:
        self.clients.add(ws)
        try:
            await ws.send(json.dumps(self.session.snapshot_json(), default=lambda o: None))
            await ws.send(json.dumps(self.status(), default=lambda o: None))
            async for raw in ws:
                try:
                    cmd = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                t = cmd.get("type")
                if t == "scenario":
                    await self.scenario(cmd)
                elif t == "cmd":
                    writes = self.session.handle_command(cmd)
                    for k, val, src, kind in writes:
                        await self.write(k, val, src, kind)
                elif t == "admin":
                    await self.admin(cmd, ws)
                await self.flush(force=True)
        except Exception as e:  # a client dropping mid-message is routine
            log.debug("client ended: %s", e)
        finally:
            self.clients.discard(ws)

    async def scenario(self, cmd: dict) -> None:
        name = cmd.get("name", "")
        if self.adapter is None or not hasattr(self.adapter, "scenario"):
            self.session.add_log("SYS", f"[sim] {name} ignored", "No session running or adapter has no scenarios.")
            return
        try:
            self.adapter.scenario(name, cmd.get("seconds"), cmd.get("params"))
            self.session.add_log("SYS", f"[sim] {name}", json.dumps(cmd.get("params") or {}))
            self.scenario_log.append({"t": round(self.session.t, 1), "name": name, "params": cmd.get("params") or {}})
            await self.send_all(self.status())
        except Exception as e:
            self.session.add_log("SYS", f"[sim] {name} not available", f"{type(e).__name__}: {e}")

    async def admin(self, cmd: dict, ws) -> None:
        name = cmd.get("name")
        if name == "status":
            await ws.send(json.dumps(self.status(), default=lambda o: None))
        elif name == "start":
            kind = cmd.get("kind", "pulse")
            opts = {"speed": float(cmd.get("speed", 1.0))}
            if kind == "pulse":
                opts["patient"] = cmd.get("patient", "DefaultMale")
                if cmd.get("init_settings"):
                    opts["init_settings"] = {k: float(v) for k, v in cmd["init_settings"].items()}
            try:
                await self.start_session(kind, **opts)
            except Exception as e:
                log.exception("start_session failed")
                await ws.send(json.dumps({"type": "admin_error", "message": f"{type(e).__name__}: {e}"}))
        elif name == "stop":
            await self.stop_session()

    async def run(self, host: str, ws_port: int, http_port: int = 0, open_browser: bool = False,
                  autostart: str | None = None, **opts) -> None:
        import websockets  # lazy: only the network layer needs it
        if http_port:
            from .webui import serve_in_thread
            serve_in_thread(host, http_port, ws_port)
            log.info("control page   http://%s:%d/", host, http_port)
            log.info("CLOVER display http://%s:%d/clover", host, http_port)
        async with websockets.serve(self.handle_client, host, ws_port, max_size=None):
            log.info("bridge websocket ws://%s:%d", host, ws_port)
            if autostart:
                await self.start_session(autostart, **opts)
            if open_browser and http_port:
                webbrowser.open(f"http://{'localhost' if host in ('0.0.0.0', '127.0.0.1') else host}:{http_port}/")
            await self.run_ticks()


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="CLOVER GUI <-> ventilator bridge")
    ap.add_argument("--adapter", default=None, help="start a session immediately: 'stub', 'pulse', or 'module.path:ClassName'")
    ap.add_argument("--patient", default="DefaultMale", help="pulse: patient state name (e.g. CSTARS-Patient3) or path")
    ap.add_argument("--speed", type=float, default=1.0, help="stub/pulse: simulation speed multiple of real time")
    ap.add_argument("--vent-optimizer", dest="root", default=None, help="pulse: path to the vent_optimizer project (default ../vent_optimizer)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--ws-port", "--port", dest="ws_port", type=int, default=8766)
    ap.add_argument("--http-port", type=int, default=8765, help="serve the control page and CLOVER display here (0 = off)")
    ap.add_argument("--open", action="store_true", help="open the control page in the default browser")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    bridge = Bridge(root=a.root)
    asyncio.run(bridge.run(a.host, a.ws_port, a.http_port, a.open, a.adapter,
                           patient=a.patient, speed=a.speed))


if __name__ == "__main__":
    main()
