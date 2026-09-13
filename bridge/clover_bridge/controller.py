"""CLOVER decision logic, ported from the GUI prototype's simulator so the two
agree exactly. Pure function: give it a ControllerInput, get a Decision with
human-readable reasoning. The bridge logs the reasoning verbatim.

Replace `decide()` with the real controller when it is available; keep the
Decision shape so the GUI and the log do not change.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

DECISION_PERIOD = 30  # seconds between scheduled decisions

# Controller ceilings/floors. These are deliberately tighter than device limits.
LIMITS = {
    "vt":   {"min": 250, "max": 800, "step": 25},
    "rr":   {"min": 6,   "max": 35,  "step": 1},
    "peep": {"min": 0,   "max": 20,  "step": 1},
    "fio2": {"min": 0.21, "max": 1.0, "step": 0.05},
}
NAMES = {"vt": "VT", "rr": "RR", "peep": "PEEP", "fio2": "FiO2", "ie": "I:E"}


def fmt(k: str, v: float) -> str:
    if k == "fio2":
        return f"{round(v * 100)}%"
    unit = {"vt": " mL", "rr": "/min", "peep": " cmH2O"}[k]
    return f"{round(v)}{unit}"


def mmss(sec: float) -> str:
    sec = max(0, int(sec))
    return f"{sec // 60}:{sec % 60:02d}"


@dataclass
class ControllerInput:
    spo2: Optional[float]            # filtered; None if no signal
    etco2: float                     # filtered
    settings: dict[str, float]       # current vt, rr, peep, fio2
    targets: dict[str, tuple[float, float]]
    override_left: dict[str, float]  # seconds remaining per key, 0 if none
    link_ok: bool = True


@dataclass
class Change:
    key: str
    frm: float
    to: float


@dataclass
class Decision:
    changes: list[Change] = field(default_factory=list)
    why: list[str] = field(default_factory=list)

    @property
    def reasoning(self) -> str:
        return " ".join(self.why)


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def decide(inp: ControllerInput) -> Decision:
    d = Decision()
    v = inp.settings
    sp, et = inp.spo2, inp.etco2
    lo_s, hi_s = inp.targets["spo2"]
    lo_c, hi_c = inp.targets["etco2"]

    if not inp.link_ok:
        d.why.append("Ventilator data not current; holding all settings.")
        return d

    def want(k: str, to: float, reason: str) -> bool:
        lim = LIMITS[k]
        to = _clamp(to, lim["min"], lim["max"])
        if abs(to - v[k]) < 1e-6:
            return False
        left = inp.override_left.get(k, 0)
        if left > 0:
            d.why.append(f"{NAMES[k]} is under user override ({mmss(left)} left); not adjusting it.")
            return False
        d.changes.append(Change(k, v[k], to))
        d.why.append(reason)
        return True

    # ---- oxygenation first ----
    if sp is None:
        d.why.append("SpO2 signal lost; holding oxygenation settings until signal returns.")
    elif sp < lo_s:
        sev = "well below" if sp < 88 else "below"
        if v["fio2"] < 1.0:
            ok = want("fio2", round((v["fio2"] + 0.10) * 20) / 20,
                      f"SpO2 {sp:.0f}% is {sev} target {lo_s:.0f}-{hi_s:.0f}%; raising FiO2 first (fastest effect).")
            if not ok and sp < 90 and v["peep"] < 15:
                want("peep", v["peep"] + 2, f"FiO2 change blocked; SpO2 {sp:.0f}%; raising PEEP to recruit lung.")
        elif v["peep"] < 15:
            want("peep", v["peep"] + 2, f"SpO2 {sp:.0f}% {sev} target and FiO2 already 100%; raising PEEP by 2.")
        else:
            d.why.append(f"SpO2 {sp:.0f}% below target but FiO2 100% and PEEP {v['peep']:.0f} are at controller limits; "
                         "no further automatic action. Clinician attention required.")
    elif sp > hi_s:
        if v["fio2"] > 0.30:
            want("fio2", round((v["fio2"] - 0.05) * 20) / 20,
                 f"SpO2 {sp:.0f}% above target {hi_s:.0f}%; weaning FiO2 by 5% to avoid hyperoxia.")
        elif v["peep"] > 5 and sp >= 97:
            want("peep", v["peep"] - 1, f"SpO2 {sp:.0f}% above target on FiO2 <=30%; weaning PEEP by 1.")
        else:
            d.why.append(f"SpO2 {sp:.0f}% slightly above target on minimal support; acceptable, no change.")
    else:
        d.why.append(f"SpO2 {sp:.0f}% within target {lo_s:.0f}-{hi_s:.0f}%.")

    # ---- ventilation ----
    if et > hi_c:
        big = et > 55
        if v["rr"] < 30:
            want("rr", v["rr"] + (4 if big else 2),
                 f"etCO2 {et:.0f} mmHg above target {hi_c:.0f}; raising RR by {4 if big else 2} to increase minute ventilation.")
        elif v["vt"] < 650:
            want("vt", v["vt"] + 50, f"etCO2 {et:.0f} mmHg high and RR at 30; raising VT by 50 mL.")
        else:
            d.why.append(f"etCO2 {et:.0f} high but RR {v['rr']:.0f} and VT {v['vt']:.0f} are at safe limits; no further automatic action.")
    elif et < lo_c:
        if v["rr"] > 10:
            want("rr", v["rr"] - 2, f"etCO2 {et:.0f} mmHg below target {lo_c:.0f}; lowering RR by 2.")
        elif v["vt"] > 400:
            want("vt", v["vt"] - 50, f"etCO2 {et:.0f} mmHg low and RR at floor; lowering VT by 50 mL.")
        else:
            d.why.append(f"etCO2 {et:.0f} low but RR {v['rr']:.0f} and VT {v['vt']:.0f} at minimum settings; no change.")
    else:
        d.why.append(f"etCO2 {et:.0f} mmHg within target {lo_c:.0f}-{hi_c:.0f}.")

    return d
