"""End-to-end test: start the bridge with the stub ventilator on a free port, connect as
the GUI would, drive every command and scenario, and check the frames that come back.

Needs `websockets`; skipped when it is not installed. Run:
    python3 -m unittest bridge/tests/test_integration.py -v
"""
import asyncio
import json
import os
import socket
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    import websockets  # noqa: F401
    HAVE_WS = True
except ImportError:
    HAVE_WS = False


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@unittest.skipUnless(HAVE_WS, "websockets not installed")
class BridgeIntegration(unittest.TestCase):
    def test_full_session(self):
        asyncio.run(self._run())

    async def _run(self):
        import websockets
        from clover_bridge.server import Bridge
        from clover_bridge.stub_adapter import StubVent

        port = free_port()
        bridge = Bridge(StubVent(speed=4.0), tick=0.25)   # 4x speed keeps the test short
        bridge.session.identity = await bridge.adapter.start()
        server = await websockets.serve(bridge.handle_client, "127.0.0.1", port)
        tasks = [asyncio.create_task(c) for c in (bridge.run_frames(), bridge.run_waves(), bridge.run_ticks())]
        try:
            async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
                last = {"frame": None, "logs": []}

                async def pump(n):
                    got = 0
                    while got < n:
                        m = json.loads(await asyncio.wait_for(ws.recv(), 10))
                        if m["type"] == "frame":
                            got += 1; last["frame"] = m
                        elif m["type"] == "log":
                            last["logs"].append(m["entry"]["title"])

                async def cmd(**kw):
                    await ws.send(json.dumps({"type": "cmd", **kw}))

                snap = json.loads(await ws.recv())
                self.assertEqual(snap["type"], "snapshot")
                self.assertEqual(snap["identity"]["make"], "SIM")
                await pump(3)
                f = last["frame"]
                self.assertTrue(f["link"]["connected"]); self.assertEqual(f["mode"], "AUTO")

                await cmd(name="set", k="fio2", value=0.6); await pump(2)
                f = last["frame"]; self.assertEqual(f["vent"]["fio2"], 0.6); self.assertIn("fio2", f["overrides"])
                self.assertEqual(f["link"]["cmd"]["status"], "ack")

                await cmd(name="release", k="fio2"); await pump(2)
                self.assertNotIn("fio2", last["frame"]["overrides"])

                await cmd(name="mode", mode="MANUAL"); await pump(1); self.assertEqual(last["frame"]["mode"], "MANUAL")
                await cmd(name="mode", mode="AUTO"); await pump(1); self.assertEqual(last["frame"]["mode"], "AUTO")

                await ws.send(json.dumps({"type": "scenario", "name": "reject"}))
                await cmd(name="set", k="vt", value=550); await pump(2)
                f = last["frame"]; self.assertEqual(f["vent"]["vt"], 500); self.assertEqual(f["link"]["cmd"]["status"], "rejected")

                await ws.send(json.dumps({"type": "scenario", "name": "panel"})); await pump(2)
                self.assertEqual(last["frame"]["link"]["remote"], "local")
                await pump(24)  # 5 s of session time at tick 0.25
                f = last["frame"]; self.assertEqual(f["vent"]["vt"], 550); self.assertIn("vt", f["overrides"])

                await cmd(name="engage", engaged=False); await pump(1)
                self.assertFalse(last["frame"]["engaged"]); self.assertIsNone(last["frame"]["nextDecisionIn"])
                await cmd(name="engage", engaged=True); await pump(1)
                self.assertTrue(last["frame"]["engaged"]); self.assertEqual(last["frame"]["mode"], "AUTO")

                await ws.send(json.dumps({"type": "scenario", "name": "alarm"})); await pump(2)
                self.assertEqual(last["frame"]["link"]["alarms"][0]["txt"], "HIGH PRESSURE")

                await ws.send(json.dumps({"type": "scenario", "name": "stale", "seconds": 120})); await pump(20)
                self.assertTrue(last["frame"]["link"]["stale"])
                self.assertTrue(any("DISENGAGE" in t for t in last["logs"]))
        finally:
            for t in tasks:
                t.cancel()
            server.close(); await server.wait_closed()
            await bridge.adapter.stop()


if __name__ == "__main__":
    unittest.main()
