"""Pulse adapter test. Runs only where the sibling vent_optimizer project (with its Pulse
build) exists; otherwise skipped. Drives the engine directly, no network:

    ../vent_optimizer/.venv/bin/python -m unittest bridge/tests/test_pulse.py -v
"""
import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from clover_bridge.pulse_adapter import DEFAULT_ROOT  # noqa: E402

HAVE_PULSE = os.path.isdir(os.path.join(DEFAULT_ROOT, "pulse_engine", "python"))


@unittest.skipUnless(HAVE_PULSE, "vent_optimizer/pulse_engine not found")
class PulseAdapterTests(unittest.TestCase):
    def test_frames_waves_write_and_disturbance(self):
        asyncio.run(self._run())

    async def _run(self):
        from clover_bridge.pulse_adapter import PulseVent
        v = PulseVent(patient="DefaultMale", speed=50.0)
        ident = await v.start()
        try:
            self.assertEqual(ident.make, "Kitware Pulse")
            frames = v.frames()
            for _ in range(3):   # first frames straddle the vent's first breaths
                f = await asyncio.wait_for(frames.__anext__(), 30)
            self.assertIn("spo2", f.measured); self.assertGreater(f.measured["spo2"], 90)
            self.assertGreater(f.measured["etco2"], 20)
            for k in ("vte", "ppeak", "pplat", "mve", "hr", "map", "paco2"):
                self.assertIsNotNone(f.measured[k], k)
            # waveforms: a real capnogram spans baseline to plateau
            cap, pleth = [], []
            waves = v.waves()
            while len(cap) < 250:
                w = await asyncio.wait_for(waves.__anext__(), 30)
                (cap if w.key == "capno" else pleth).extend(s for s in w.samples if s == s)
            self.assertLess(min(cap), 5); self.assertGreater(max(cap), 25)
            self.assertGreaterEqual(min(pleth), 0.0); self.assertLessEqual(max(pleth), 1.0)
            # writes: ack in range, reject out of range, read-back in next frame
            self.assertEqual((await v.set_setting("fio2", 0.6)).status, "ack")
            self.assertEqual((await v.set_setting("vt", 2000)).status, "rejected")
            self.assertEqual((await v.set_setting("bogus", 1)).status, "unsupported")
            for _ in range(10):   # read forward past any frame produced before the write
                f = await asyncio.wait_for(frames.__anext__(), 30)
                if f.settings["fio2"] == 0.6:
                    break
            self.assertEqual(f.settings["fio2"], 0.6); self.assertEqual(f.settings["vt"], 500)
            # disturbance: ARDS lowers SpO2 over a few simulated minutes at fixed FiO2
            v.scenario("injury")
            base = f.measured["spo2"]; lowest = base
            for _ in range(240):
                f = await asyncio.wait_for(frames.__anext__(), 30)
                if f.measured["spo2"] is not None:
                    lowest = min(lowest, f.measured["spo2"])
            self.assertLess(lowest, base - 1.0, f"ARDS should desaturate: base {base:.1f}, lowest {lowest:.1f}")
            # alarms from the ventilator model appear as Alarm records
            self.assertIsInstance(f.alarms, list)
        finally:
            await v.stop()


if __name__ == "__main__":
    unittest.main()
