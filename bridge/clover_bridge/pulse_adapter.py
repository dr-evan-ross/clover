"""Pulse Physiology Engine as patient AND ventilator, behind the VentAdapter interface.

Uses the Pulse build and helpers in the sibling ``vent_optimizer`` project (its
``common.pulse_env`` bootstrap, ``scenario_runner.actions`` disturbance vocabulary),
so run the bridge with that project's Python:

    ../vent_optimizer/.venv/bin/python bridge/run_bridge.py --adapter pulse --patient DefaultMale

What the GUI gets from this adapter that no hand-written simulator gives it:
  * a genuine capnogram (CO2 partial pressure at the carina, sampled at 50 Hz)
  * a pulse waveform (arterial pressure, normalised, standing in for the pleth)
  * ventilator measurements from Pulse's own ventilator model (VTe, PIP, Pplat, Pmean)
  * disturbances with real physiology: ARDS, hemorrhage, obstruction, pneumothorax
  * 28 pre-stabilised patients

Pulse is not thread-safe; every engine call happens on the asyncio loop thread, in
``_run`` or in ``set_setting`` between steps.
"""
from __future__ import annotations

import asyncio
import collections
import math
import os
import random
import sys
import time
from typing import AsyncIterator, Optional

from .adapter import (Alarm, CommandResult, Frame, Identity, VentAdapter,
                      WaveChunk)

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ROOT = os.environ.get("CLOVER_VENT_OPTIMIZER") or os.path.normpath(
    os.path.join(HERE, "..", "..", "..", "vent_optimizer"))

# Device-range limits the "ventilator" enforces (a real vent rejects outside these).
DEVICE_RANGE = {"vt": (100, 1500), "rr": (4, 50), "peep": (0, 30), "fio2": (0.21, 1.0), "ie": (1.0, 4.0)}

_NAN = float("nan")


def _f(v) -> Optional[float]:
    """NaN/inf -> None for JSON."""
    return None if v is None or (isinstance(v, float) and (v != v or math.isinf(v))) else v


class PulseVent(VentAdapter):
    def __init__(self, patient: str = "DefaultMale", root: str | None = None,
                 speed: float = 1.0, sample_hz: int = 50, init_settings: dict | None = None,
                 seed: int | None = 1):
        self.patient = patient
        self.root = root or DEFAULT_ROOT
        self.speed = float(speed)
        self.sample_hz = int(sample_hz)
        self.rng = random.Random(seed)
        self.settings = {"vt": 500.0, "rr": 14.0, "peep": 5.0, "fio2": 0.40, "ie": 2.0}
        if init_settings:
            self.settings.update(init_settings)
        self.flow_lpm = 50.0
        self.t = 0.0                      # simulated seconds since start
        self.sim_steps = 0
        self._fq: asyncio.Queue = asyncio.Queue(maxsize=4)     # shallow: a slow consumer gets fresh frames, not a backlog
        self._wq: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._last: dict = {}
        self._abp_win: collections.deque = collections.deque(maxlen=self.sample_hz * 3)
        # scenario state
        self.probe_off_until = 0.0
        self.stale_until = 0.0
        self.reject_next = False
        self.remote = "granted"
        self._remote_until = 0.0
        self.extra_alarms: list[tuple[Alarm, float]] = []
        self.events: list[dict] = []

    # ------------------------------------------------------------------ VentAdapter
    async def start(self) -> Identity:
        if self.root not in sys.path:
            sys.path.insert(0, self.root)
        from common import pulse_env                       # noqa: E402  (vent_optimizer)
        pulse_env.bootstrap(chdir=True)                    # Pulse wants cwd = pulse_engine/bin
        from pulse.engine.PulseEngine import PulseEngine  # noqa: E402
        from pulse.cdm.engine import SEDataRequest, SEDataRequestManager  # noqa: E402
        from pulse.cdm import scalars as S                 # noqa: E402
        from scenario_runner.actions import build_action_map  # noqa: E402

        P, V = SEDataRequest.create_physiology_request, SEDataRequest.create_mechanical_ventilator_request
        spec = [
            ("spo2",   P("OxygenSaturation")),
            ("etco2",  P("EndTidalCarbonDioxidePressure", unit=S.PressureUnit.mmHg)),
            ("abp",    P("ArterialPressure", unit=S.PressureUnit.mmHg)),                 # waveform -> pleth proxy
            ("co2",    SEDataRequest.create_gas_compartment_substance_request(
                           "Carina", "CarbonDioxide", "PartialPressure", unit=S.PressureUnit.mmHg)),  # capnogram
            ("hr",     P("HeartRate", unit=S.FrequencyUnit.Per_min)),
            ("map",    P("MeanArterialPressure", unit=S.PressureUnit.mmHg)),
            ("pao2",   P("ArterialOxygenPressure", unit=S.PressureUnit.mmHg)),
            ("paco2",  P("ArterialCarbonDioxidePressure", unit=S.PressureUnit.mmHg)),
            ("ph",     P("BloodPH")),
            ("paw",    V("AirwayPressure", unit=S.PressureUnit.cmH2O)),
            ("vte",    V("ExpiratoryTidalVolume", unit=S.VolumeUnit.mL)),
            ("vti",    V("InspiratoryTidalVolume", unit=S.VolumeUnit.mL)),
            ("pip",    V("PeakInspiratoryPressure", unit=S.PressureUnit.cmH2O)),
            ("pplat",  V("PlateauPressure", unit=S.PressureUnit.cmH2O)),
            ("pmean",  V("MeanAirwayPressure", unit=S.PressureUnit.cmH2O)),
            ("peep_t", V("TotalPositiveEndExpiratoryPressure", unit=S.PressureUnit.cmH2O)),
            ("vrr",    V("RespirationRate", unit=S.FrequencyUnit.Per_min)),
        ]
        self.keys = [k for k, _ in spec]
        drm = SEDataRequestManager([r for _, r in spec])
        self.eng = PulseEngine()
        self.eng.log_to_console(False)
        path = self.patient if self.patient.endswith(".json") else f"./states/{self.patient}@0s.json"
        if not self.eng.serialize_from_file(path, drm):
            raise RuntimeError(f"Pulse could not load patient state {path!r} (cwd {os.getcwd()})")
        self.m = build_action_map()
        self._apply_vent()
        self.eng.advance_time_s(8.0)       # settle: end-tidal CO2 needs a full breath or two after the vent connects
        self._pull()
        self._task = asyncio.create_task(self._run())
        name = os.path.basename(self.patient).replace("@0s.json", "").replace(".json", "")
        return Identity(make="Kitware Pulse", model=f"Physiology Engine · {name}", serial=name,
                        firmware="pulse_engine build", transport="in-process",
                        supports_remote_state=False, writable=("vt", "rr", "peep", "fio2", "ie"),
                        extra={"patient_state": path, "sample_hz": self.sample_hz})

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()

    async def frames(self) -> AsyncIterator[Frame]:
        while not self._stop.is_set():
            yield await self._fq.get()

    async def waves(self) -> AsyncIterator[WaveChunk]:
        while not self._stop.is_set():
            yield await self._wq.get()

    async def set_setting(self, key: str, value: float) -> CommandResult:
        if key not in self.settings:
            return CommandResult("unsupported", f"{key} is not a writable setting")
        lo, hi = DEVICE_RANGE[key]
        if not (lo <= value <= hi):
            return CommandResult("rejected", f"{key} {value} outside device range {lo}-{hi}", readback=self.settings[key])
        if self.reject_next:
            self.reject_next = False
            return CommandResult("rejected", "device refused command (simulated)", readback=self.settings[key])
        await asyncio.sleep(0.2 / self.speed)   # serial round trip
        self.settings[key] = float(value)
        self._apply_vent()
        return CommandResult("ack", "", readback=self.settings[key])

    # ------------------------------------------------------------------ scenarios (demo only)
    def scenario(self, name: str, seconds: float | None = None) -> None:
        from scenario_runner.actions import apply_event, Synth
        ev = None
        if name == "injury":          ev = Synth("ards", {"severity": 0.6})
        elif name == "recover":       ev = Synth("ards", {"severity": 0.0})
        elif name == "hypo":          ev = Synth("exercise", {"intensity": 0.35})   # Pulse's VCO2 knob
        elif name == "hemorrhage":    ev = Synth("hemorrhage", {"compartment": "RightLeg", "rate_mL_per_min": 250.0})
        elif name == "stop_bleed":    ev = Synth("hemorrhage", {"compartment": "RightLeg", "rate_mL_per_min": 0.0})
        elif name == "obstruction":   ev = Synth("airway_obstruction", {"severity": 0.6})
        elif name == "pneumo":        ev = Synth("tension_pneumothorax", {"side": "Left", "severity": 0.7})
        elif name == "decompress":    ev = Synth("needle_decompression", {"side": "Left", "state": "on"})
        elif name == "dropout":       self.probe_off_until = self.t + (seconds or 40)
        elif name == "stale":         self.stale_until = self.t + (seconds or 15)
        elif name == "reject":        self.reject_next = True
        elif name == "panel":
            self.settings["vt"] = 550.0; self._apply_vent()
            self.remote, self._remote_until = "local", self.t + 20
        elif name == "alarm":         self.extra_alarms.append((Alarm("sim", "HIGH PRESSURE", "crit"), self.t + (seconds or 30)))
        else:
            raise ValueError(f"unknown scenario {name!r}")
        if ev is not None:
            apply_event(self.eng, self.m, ev)
        self.events.append({"t": round(self.t, 1), "scenario": name})

    # ------------------------------------------------------------------ engine
    def _apply_vent(self) -> None:
        from pulse.cdm.mechanical_ventilator_actions import eMechanicalVentilator_VolumeControlMode
        m, s = self.m, self.settings
        ti = (60.0 / s["rr"]) / (1.0 + s["ie"])       # I:E 1:x -> inspiratory time
        v = m["VC"]()
        v.set_connection(m["On"])
        v.set_mode(eMechanicalVentilator_VolumeControlMode.ContinuousMandatoryVentilation)
        v.get_fraction_inspired_oxygen().set_value(s["fio2"])
        v.get_inspiratory_period().set_value(ti, m["TimeUnit"].s)
        v.get_tidal_volume().set_value(s["vt"], m["VolumeUnit"].mL)
        v.get_positive_end_expired_pressure().set_value(s["peep"], m["PressureUnit"].cmH2O)
        v.get_respiration_rate().set_value(s["rr"], m["FrequencyUnit"].Per_min)
        v.get_flow().set_value(self.flow_lpm, m["VolumePerTimeUnit"].L_Per_min)
        self.eng.process_action(v)

    def _pull(self) -> dict:
        raw = self.eng.pull_data()
        d = {k: raw[i + 1] for i, k in enumerate(self.keys)}
        self._last = d
        return d

    def _pleth(self, abp: float) -> float:
        if self.t < self.probe_off_until:
            return 0.5 + (self.rng.random() - 0.5) * 0.06
        if abp != abp:
            return _NAN
        self._abp_win.append(abp)
        lo, hi = min(self._abp_win), max(self._abp_win)
        if hi - lo < 1.0:
            return 0.5
        return max(0.0, min(1.0, 0.1 + 0.8 * (abp - lo) / (hi - lo)))

    def frame(self) -> Frame:
        d = self._last
        s = self.settings
        probe_off = self.t < self.probe_off_until
        vte = d.get("vte", _NAN)
        vrr = d.get("vrr", _NAN)
        measured = {
            "spo2": None if probe_off else _f(d.get("spo2", _NAN) * 100.0 if d.get("spo2", _NAN) == d.get("spo2", _NAN) else _NAN),
            "etco2": _f(d.get("etco2")),
            "mve": _f((vrr if vrr == vrr else s["rr"]) * vte / 1000.0) if vte == vte else None,
            "vte": _f(vte), "leak": 0.0,
            "ppeak": _f(d.get("pip")), "pplat": _f(d.get("pplat")), "pmean": _f(d.get("pmean")),
            # richer than the GUI needs; passes through untouched
            "hr": _f(d.get("hr")), "map": _f(d.get("map")), "pao2": _f(d.get("pao2")),
            "paco2": _f(d.get("paco2")), "ph": _f(d.get("ph")), "peep_total": _f(d.get("peep_t")), "vti": _f(d.get("vti")),
        }
        alarms: list[Alarm] = []
        pip = d.get("pip", _NAN)
        if pip == pip and pip > 40:
            alarms.append(Alarm("hp", f"HIGH PRESSURE {pip:.0f}", "crit"))
        if vte == vte and vte < 0.5 * s["vt"]:
            alarms.append(Alarm("lowvt", f"LOW VTE {vte:.0f} mL", "warn"))
        self.extra_alarms = [(a, until) for a, until in self.extra_alarms if self.t < until]
        alarms += [a for a, _ in self.extra_alarms]
        if self.remote == "local" and self.t >= self._remote_until:
            self.remote = "granted"
        self.sim_steps += 1
        return Frame(settings=dict(s), measured=measured, remote=self.remote, alarms=alarms,
                     seq=self.sim_steps, ts=time.time())

    async def _run(self) -> None:
        """Advance Pulse in 100 ms chunks, pacing to ``speed`` x real time."""
        dt = 1.0 / self.sample_hz
        per_chunk = max(1, int(round(0.1 / dt)))
        next_frame_t = self.t + 1.0
        wall_next = time.monotonic()
        try:
            while not self._stop.is_set():
                pleth, capno = [], []
                for _ in range(per_chunk):
                    self.eng.advance_time_s(dt)
                    self.t += dt
                    d = self._pull()
                    pleth.append(self._pleth(d.get("abp", _NAN)))
                    c = d.get("co2", _NAN)
                    capno.append(max(0.0, c) if c == c else _NAN)
                stalled = self.t < self.stale_until
                if not stalled:
                    self._offer(self._wq, WaveChunk("pleth", self.sample_hz, pleth))
                    self._offer(self._wq, WaveChunk("capno", self.sample_hz, capno))
                if self.t >= next_frame_t:
                    next_frame_t += 1.0
                    if not stalled:
                        self._offer(self._fq, self.frame())
                wall_next += 0.1 / self.speed
                lag = wall_next - time.monotonic()
                if lag > 0:
                    await asyncio.sleep(lag)
                else:
                    wall_next = time.monotonic()    # fell behind: don't try to catch up in a burst
                    await asyncio.sleep(0)
        except asyncio.CancelledError:
            pass

    @staticmethod
    def _offer(q: asyncio.Queue, item) -> None:
        if q.full():
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                pass
        q.put_nowait(item)
