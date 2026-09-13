"""Standard-library tests for the bridge core. Run:  python3 -m unittest discover bridge/tests"""
import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from clover_bridge.adapter import CommandResult, Frame  # noqa: E402
from clover_bridge.controller import ControllerInput, decide  # noqa: E402
from clover_bridge.session import Session  # noqa: E402
from clover_bridge.stub_adapter import StubVent  # noqa: E402


def base_input(**kw):
    d = dict(spo2=95.0, etco2=40.0, settings={"vt": 500, "rr": 14, "peep": 5, "fio2": 0.40},
             targets={"spo2": (92, 96), "etco2": (35, 45)}, override_left={}, link_ok=True)
    d.update(kw)
    return ControllerInput(**d)


class ControllerTests(unittest.TestCase):
    def test_in_range_no_change(self):
        d = decide(base_input())
        self.assertEqual(d.changes, [])
        self.assertIn("within target", d.reasoning)

    def test_desaturation_raises_fio2_first(self):
        d = decide(base_input(spo2=88.0))
        self.assertEqual([c.key for c in d.changes], ["fio2"])
        self.assertAlmostEqual(d.changes[0].to, 0.50)

    def test_override_blocks_fio2_and_falls_back_to_peep(self):
        d = decide(base_input(spo2=87.0, override_left={"fio2": 120}))
        self.assertEqual([c.key for c in d.changes], ["peep"])
        self.assertIn("under user override", d.reasoning)

    def test_hypercapnia_raises_rr(self):
        d = decide(base_input(etco2=52.0))
        self.assertEqual([c.key for c in d.changes], ["rr"])
        self.assertEqual(d.changes[0].to, 16)

    def test_holds_when_link_not_ok(self):
        d = decide(base_input(spo2=80.0, link_ok=False))
        self.assertEqual(d.changes, [])


class StubAdapterTests(unittest.TestCase):
    def test_frame_shape_and_scenarios(self):
        v = StubVent(speed=100)
        fr = v.frame()
        self.assertEqual(set(fr.settings), {"vt", "rr", "peep", "fio2", "ie"})
        for k in ("spo2", "etco2", "mve", "vte", "leak", "ppeak", "pplat"):
            self.assertIn(k, fr.measured)
        v.scenario("injury")
        for _ in range(600):
            v._step(1.0)
        self.assertLess(v.spo2, 92, "injury at FiO2 0.40 should desaturate")
        v.scenario("alarm")
        self.assertEqual(v.frame().alarms[0].text, "HIGH PRESSURE")

    def test_set_setting_and_reject(self):
        v = StubVent(speed=1000)
        res = asyncio.run(v.set_setting("fio2", 0.6))
        self.assertEqual(res.status, "ack")
        self.assertEqual(v.settings["fio2"], 0.6)
        v.scenario("reject")
        res = asyncio.run(v.set_setting("vt", 550))
        self.assertEqual(res.status, "rejected")
        self.assertEqual(v.settings["vt"], 500)
        self.assertEqual(asyncio.run(v.set_setting("bogus", 1)).status, "unsupported")


class SessionTests(unittest.TestCase):
    def frame(self, spo2=95.0, etco2=40.0, **settings):
        st = {"vt": 500, "rr": 14, "peep": 5, "fio2": 0.40, "ie": 2.0}
        st.update(settings)
        return Frame(settings=st, measured={"spo2": spo2, "etco2": etco2, "ppeak": 22}, remote="granted")

    def test_first_cycle_no_change_and_frame_json(self):
        s = Session()
        for _ in range(3):
            s.on_frame(self.frame()); s.tick(1.0)
        self.assertTrue(any(e.title == "No change" for e in s.log))
        j = s.frame_json()
        self.assertEqual(j["type"], "frame"); self.assertTrue(j["link"]["connected"]); self.assertFalse(j["link"]["stale"])

    def test_out_of_range_interrupt_fires_early(self):
        s = Session()
        s.on_frame(self.frame()); s.tick(1.0)           # scheduled decision at t=1
        for _ in range(6):
            s.on_frame(self.frame(spo2=86.0)); s.tick(1.0)  # EMA drops below 92 -> interrupt
        early = [e for e in s.log if e.src == "CTRL" and "EARLY DECISION" in e.detail]
        self.assertTrue(early, "desaturation must trigger an early decision")

    def test_user_set_in_auto_becomes_override_after_ack(self):
        s = Session()
        s.on_frame(self.frame()); s.tick(1.0)
        writes = s.handle_command({"type": "cmd", "name": "set", "k": "fio2", "value": 0.6})
        self.assertEqual(writes[0][:2], ("fio2", 0.6))
        s.on_command_result("fio2", 0.6, "USER", "override", CommandResult("ack"))
        self.assertGreater(s.override_left("fio2"), 0)
        self.assertTrue(any(e.title.startswith("OVERRIDE FiO2") for e in s.log))
        s.handle_command({"type": "cmd", "name": "release", "k": "fio2"})
        self.assertEqual(s.override_left("fio2"), 0)

    def test_manual_clears_overrides_and_advises(self):
        s = Session()
        s.on_frame(self.frame()); s.tick(1.0)
        s.on_command_result("vt", 550, "USER", "override", CommandResult("ack"))
        s.handle_command({"type": "cmd", "name": "mode", "mode": "MANUAL"})
        self.assertEqual(s.overrides, {})
        for _ in range(31):
            s.on_frame(self.frame(etco2=55.0, vt=550)); s.tick(1.0)
        self.assertTrue(any("Advisory" in e.title for e in s.log if e.src == "CTRL"))
        self.assertIn("rr", s.suggest)

    def test_disengage_and_engage(self):
        s = Session()
        s.on_frame(self.frame()); s.tick(1.0)
        s.handle_command({"type": "cmd", "name": "engage", "engaged": False})
        self.assertFalse(s.engaged)
        self.assertEqual(s.handle_command({"type": "cmd", "name": "set", "k": "rr", "value": 16}), [])
        s.handle_command({"type": "cmd", "name": "engage", "engaged": True})
        self.assertTrue(s.engaged); self.assertEqual(s.mode, "AUTO")

    def test_stale_and_mismatch_adoption(self):
        s = Session()
        s.on_frame(self.frame()); s.tick(1.0)
        for _ in range(4):
            s.tick(1.0)
        self.assertTrue(s.stale()); self.assertFalse(s.link_ok())
        for _ in range(6):
            s.on_frame(self.frame(vt=550)); s.tick(1.0)
        self.assertEqual(s.settings["vt"], 550)
        self.assertGreater(s.override_left("vt"), 0, "panel change becomes a user override in AUTO")
        self.assertTrue(any("Vent panel changed VT" in e.title for e in s.log))

    def test_rejected_command_leaves_setting(self):
        s = Session()
        s.on_frame(self.frame()); s.tick(1.0)
        s.on_command_result("vt", 550, "CTRL", "reason", CommandResult("rejected", "out of device range"))
        self.assertEqual(s.settings["vt"], 500); self.assertEqual(s.cmd["status"], "rejected")


if __name__ == "__main__":
    unittest.main()
