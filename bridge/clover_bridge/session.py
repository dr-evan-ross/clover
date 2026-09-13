"""Session: everything the GUI shows, held in one place and driven by frames
from the adapter and commands from the GUI. Produces the JSON messages in
CONTRACT.md. Standard library only.

Authority model (matches the GUI's design rules):
  engaged   - CLOVER is attached to the vent. False = DISENGAGED: no commands,
              no advisories; GUI controls greyed; vent runs on its own panel.
  mode      - 'AUTO' (controller applies) | 'MANUAL' (controller advises only)
  overrides - per setting, the time an operator adjustment keeps it from the
              controller. Exists only while engaged and in AUTO.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .adapter import CommandResult, Frame, Identity
from .controller import DECISION_PERIOD, NAMES, ControllerInput, Decision, decide, fmt, mmss

STALE_AFTER = 3.0      # s without a frame before the stream is called stale
MISMATCH_CONFIRM = 5.0  # s a read-back mismatch must persist before we adopt it
DISPLAY_ALPHA = 0.2     # EMA for displayed vitals
DISPLAY_HYST = 0.75     # shown integer changes when EMA has moved this far

IE_VALUES = ["1:1", "1:1.5", "1:2", "1:2.5", "1:3", "1:4"]
IE_NUMERIC = [1.0, 1.5, 2.0, 2.5, 3.0, 4.0]


@dataclass
class LogEntry:
    t: float
    src: str      # CTRL | USER | ALERT | SYS | VENT
    title: str
    detail: str = ""

    def json(self) -> dict:
        return {"t": round(self.t, 1), "src": self.src, "title": self.title, "detail": self.detail}


@dataclass
class Session:
    identity: Optional[Identity] = None
    start_wall: float = field(default_factory=time.time)
    t: float = 0.0
    engaged: bool = True
    mode: str = "AUTO"
    mode_since: float = 0.0
    run_since: float = 0.0
    targets: dict[str, list[float]] = field(default_factory=lambda: {"spo2": [92, 96], "etco2": [35, 45]})
    cfg: dict[str, Any] = field(default_factory=lambda: {"overrideMin": 5})
    # what CLOVER believes the settings are; the vent's read-back may disagree
    settings: dict[str, float] = field(default_factory=lambda: {"vt": 500, "rr": 14, "peep": 5, "fio2": 0.40, "ie": 2.0})
    set_by: dict[str, dict] = field(default_factory=dict)
    overrides: dict[str, float] = field(default_factory=dict)  # key -> until (session seconds)
    suggest: dict[str, float] = field(default_factory=dict)
    # vitals
    raw: dict[str, Optional[float]] = field(default_factory=lambda: {"spo2": None, "etco2": None})
    filt: dict[str, Optional[float]] = field(default_factory=lambda: {"spo2": None, "etco2": None})
    shown: dict[str, Optional[int]] = field(default_factory=lambda: {"spo2": None, "etco2": None})
    measured: dict[str, Any] = field(default_factory=dict)
    hist: list[dict] = field(default_factory=list)
    # link
    last_frame_t: Optional[float] = None
    connected: bool = False
    cmd: dict[str, Any] = field(default_factory=lambda: {"status": "none"})
    remote: Optional[str] = None
    mismatch: Optional[dict] = None
    vent_alarms: list[dict] = field(default_factory=list)
    # controller
    last_decision: float = -DECISION_PERIOD
    prev_state: dict[str, str] = field(default_factory=lambda: {"spo2": "ok", "etco2": "ok"})
    log: list[LogEntry] = field(default_factory=list)
    _new_log: list[LogEntry] = field(default_factory=list)

    # ------------------------------------------------------------ logging
    def add_log(self, src: str, title: str, detail: str = "") -> None:
        e = LogEntry(self.t, src, title, detail)
        self.log.append(e)
        self._new_log.append(e)
        if len(self.log) > 20000:
            del self.log[:1000]

    def drain_log(self) -> list[dict]:
        out = [e.json() for e in self._new_log]
        self._new_log.clear()
        return out

    # ------------------------------------------------------------ helpers
    def override_left(self, k: str) -> float:
        until = self.overrides.get(k)
        if until is None:
            return 0.0
        left = until - self.t
        if left <= 0:
            del self.overrides[k]
            self.add_log("SYS", f"{NAMES[k]} override expired", f"Controller resumes adjusting {NAMES[k]}.")
            return 0.0
        return left

    def link_ok(self) -> bool:
        return self.connected and not self.stale()

    def stale(self) -> bool:
        return self.connected and self.last_frame_t is not None and (self.t - self.last_frame_t) > STALE_AFTER

    def state_of(self, k: str) -> str:
        v = self.shown.get(k)
        if v is None:
            return "lost"
        lo, hi = self.targets[k]
        if k == "spo2":
            return "crit" if v < 88 else "warn" if v < lo else "ok"
        return "crit" if (v > 60 or v < 25) else "warn" if (v > hi + 5 or v < lo - 5) else "ok"

    # ------------------------------------------------------------ inputs
    def on_frame(self, fr: Frame) -> None:
        was_connected = self.connected
        self.connected = True
        self.last_frame_t = self.t
        if not was_connected:
            self.add_log("SYS", "Ventilator link up", f"{self.identity.make if self.identity else ''} {self.identity.model if self.identity else ''} streaming.")
        # vitals
        for k in ("spo2", "etco2"):
            v = fr.measured.get(k)
            self.raw[k] = v
            if v is None:
                self.filt[k] = None
                self.shown[k] = None
                continue
            f = self.filt[k]
            f = v if f is None else f + (v - f) * DISPLAY_ALPHA
            self.filt[k] = f
            if self.shown[k] is None or abs(f - self.shown[k]) >= DISPLAY_HYST:
                self.shown[k] = round(f)
        self.hist.append({"t": round(self.t, 1), "spo2": self.filt["spo2"], "etco2": self.filt["etco2"]})
        if len(self.hist) > 3700:
            del self.hist[0]
        self.measured = {k: v for k, v in fr.measured.items() if k not in ("spo2", "etco2")}
        # read-back vs belief
        for k in ("vt", "rr", "peep", "fio2", "ie"):
            if k in fr.settings and k in self.settings and abs(fr.settings[k] - self.settings[k]) > 1e-6:
                if self.mismatch is None or self.mismatch["k"] != k:
                    self.mismatch = {"k": k, "val": fr.settings[k], "at": self.t}
                break
        else:
            self.mismatch = None
        self.remote = fr.remote
        self.vent_alarms = [{"id": a.id, "txt": a.text, "sev": a.severity} for a in fr.alarms]

    def on_wave(self, key: str, rate: float, samples: list[float]) -> dict:
        gui_key = {"pleth": "spo2", "capno": "etco2"}.get(key, key)
        return {"type": "wave", "k": gui_key, "rate": rate, "samples": [round(s, 3) for s in samples]}

    # ------------------------------------------------------------ time
    def tick(self, dt: float) -> list[Decision]:
        """Advance session time; run timers and the controller. Returns decisions made."""
        self.t += dt
        out: list[Decision] = []
        if self.connected and self.last_frame_t is not None and self.t - self.last_frame_t > 30:
            self.connected = False
            self.add_log("SYS", "Ventilator link lost", "No frames for 30 s.")
        if self.mismatch and self.t - self.mismatch["at"] >= MISMATCH_CONFIRM:
            k, val = self.mismatch["k"], self.mismatch["val"]
            frm = self.settings[k]
            self.settings[k] = val
            self.set_by[k] = {"src": "USER", "t": self.t}
            if self.engaged and self.mode == "AUTO":
                self.overrides[k] = self.t + self.cfg["overrideMin"] * 60
            self.add_log("SYS", f"Vent panel changed {NAMES[k]} {fmt(k, frm) if k != 'ie' else frm} -> {fmt(k, val) if k != 'ie' else val}",
                         "Read-back mismatch confirmed; CLOVER adopts the ventilator's value and treats it as a user override.")
            self.mismatch = None
        # out-of-range interrupt then scheduled cycle
        worse = []
        rank = {"ok": 0, "lost": 1, "warn": 1, "crit": 2}
        for k in ("spo2", "etco2"):
            st = self.state_of(k)
            if rank[st] > rank[self.prev_state[k]]:
                worse.append("SpO2" if k == "spo2" else "etCO2")
            self.prev_state[k] = st
        if self.engaged and worse and self.t - self.last_decision > 0:
            out.append(self.run_controller(f"{' and '.join(worse)} left target range"))
        elif self.engaged and self.t - self.last_decision >= DECISION_PERIOD:
            out.append(self.run_controller())
        return out

    # ------------------------------------------------------------ controller
    def controller_input(self) -> ControllerInput:
        return ControllerInput(
            spo2=self.filt["spo2"], etco2=self.filt["etco2"] if self.filt["etco2"] is not None else 40.0,
            settings={k: self.settings[k] for k in ("vt", "rr", "peep", "fio2")},
            targets={k: tuple(v) for k, v in self.targets.items()},
            override_left={k: self.override_left(k) for k in ("vt", "rr", "peep", "fio2")},
            link_ok=self.link_ok(),
        )

    def run_controller(self, trigger: str | None = None) -> Decision:
        remaining = DECISION_PERIOD - (self.t - self.last_decision)
        self.last_decision = self.t
        d = decide(self.controller_input())
        prefix = f"EARLY DECISION - {trigger} with {mmss(remaining)} left in the {DECISION_PERIOD} s cycle. " if trigger else ""
        reasoning = prefix + d.reasoning
        if not self.link_ok():
            self.add_log("CTRL", "Holding - ventilator data not current", reasoning)
            d.changes.clear()
            return d
        if self.mode == "AUTO":
            if d.changes:
                self.add_log("CTRL", "Decision: " + ", ".join(f"{NAMES[c.key]} {fmt(c.key, c.frm)} -> {fmt(c.key, c.to)}" for c in d.changes), reasoning)
            else:
                self.add_log("CTRL", "No change", reasoning)
            # the server sends the changes to the adapter and logs each ack/reject
        else:
            self.suggest = {c.key: c.to for c in d.changes}
            if d.changes:
                self.add_log("CTRL", "Advisory (MANUAL, not applied): " + ", ".join(f"{NAMES[c.key]} -> {fmt(c.key, c.to)}" for c in d.changes), reasoning)
            else:
                self.add_log("CTRL", "Advisory: no change recommended", reasoning)
            d.changes.clear()
        return d

    # ------------------------------------------------------------ commands from GUI
    def handle_command(self, cmd: dict) -> list[tuple[str, float, str, str]]:
        """Apply a GUI command to session state. Returns a list of writes to send
        to the adapter as (key, value, src, detail). Everything else is done here."""
        name = cmd.get("name")
        writes: list[tuple[str, float, str, str]] = []
        if name == "set":
            k, val = cmd["k"], float(cmd["value"])
            if not self.engaged or not self.link_ok():
                self.add_log("USER", f"{NAMES[k]} change refused", "CLOVER disengaged or ventilator data not current.")
                return writes
            is_override = self.mode == "AUTO"
            writes.append((k, val, "USER", "override" if is_override else "manual"))
        elif name == "release":
            k = cmd["k"]
            left = self.override_left(k)
            if left > 0:
                del self.overrides[k]
                self.last_decision = self.t - DECISION_PERIOD
                self.add_log("USER", f"{NAMES[k]} returned to CLOVER", f"Operator cancelled the override lockout with {mmss(left)} remaining. Controller re-evaluates immediately.")
        elif name == "mode":
            if not self.engaged:
                return writes
            self.mode = "MANUAL" if cmd.get("mode") == "MANUAL" else "AUTO"
            self.mode_since = self.t
            self.suggest.clear()
            if self.mode == "MANUAL":
                held = [k for k in list(self.overrides) if self.override_left(k) > 0]
                self.overrides.clear()
                if held:
                    self.add_log("SYS", "Overrides cleared", ", ".join(NAMES[k] for k in held) + " ended: in MANUAL the operator holds every setting.")
            self.last_decision = self.t - DECISION_PERIOD
            self.add_log("USER", f"Mode -> {'AUTOMATIC' if self.mode == 'AUTO' else 'MANUAL'}",
                         "Controller will apply its decisions." if self.mode == "AUTO" else "Controller computes and logs recommendations but does not apply them.")
        elif name == "engage":
            want = bool(cmd.get("engaged"))
            if want == self.engaged:
                return writes
            self.engaged = want
            self.run_since = self.t
            settings = ", ".join(f"{NAMES[k]} {fmt(k, self.settings[k])}" for k in ("vt", "rr", "peep", "fio2"))
            if want:
                self.mode, self.mode_since = "AUTO", self.t
                self.overrides.clear(); self.suggest.clear()
                self.last_decision = self.t - DECISION_PERIOD
                self.add_log("USER", "ENGAGE - CLOVER in control", f"Settings read back from the ventilator: {settings}. FULL AUTO; first decision immediately.")
            else:
                held = [k for k in list(self.overrides) if self.override_left(k) > 0]
                self.overrides.clear(); self.suggest.clear()
                if held:
                    self.add_log("SYS", "Overrides cleared", ", ".join(NAMES[k] for k in held) + " ended because CLOVER is disengaged.")
                self.add_log("USER", "DISENGAGE - CLOVER released the ventilator", f"No further commands or advisories from CLOVER. Ventilator continues on its own front-panel controls at: {settings}.")
        elif name == "update":
            if self.engaged:
                self.add_log("USER", "Update requested", f"Operator tapped UPDATE with {mmss(DECISION_PERIOD - (self.t - self.last_decision))} left in the cycle.")
                d = self.run_controller("operator requested an update")
                writes.extend((c.key, c.to, "CTRL", d.reasoning) for c in d.changes)
        elif name == "target":
            k = cmd["k"]; lo, hi = float(cmd["lo"]), float(cmd["hi"])
            frm = f"{self.targets[k][0]:.0f}-{self.targets[k][1]:.0f}"
            self.targets[k] = [lo, hi]
            self.add_log("USER", f"{'SpO2' if k == 'spo2' else 'etCO2'} target {frm} -> {lo:.0f}-{hi:.0f}", "Operator changed the target range.")
        elif name == "ie":
            idx = int(cmd["idx"])
            if self.engaged and self.link_ok():
                writes.append(("ie", IE_NUMERIC[idx], "USER", "manual"))
        elif name == "cfg":
            frm = self.cfg["overrideMin"]
            self.cfg["overrideMin"] = int(cmd["overrideMin"])
            self.add_log("USER", f"SETUP: override lockout {frm} -> {self.cfg['overrideMin']} min", "Applies to overrides started from now on.")
        elif name == "o2_emergency":
            if not self.engaged or not self.link_ok():
                self.add_log("USER", "Emergency O2 refused", "CLOVER disengaged or ventilator data not current. Set FiO2 on the ventilator itself.")
            else:
                writes.append(("fio2", 1.0, "USER", "emergency"))
        elif name == "log":
            self.add_log("USER", str(cmd.get("title", ""))[:120], str(cmd.get("detail", ""))[:400])
        return writes

    # ------------------------------------------------------------ results of writes
    def on_command_result(self, k: str, val: float, src: str, kind: str, res: CommandResult) -> None:
        label = NAMES.get(k, k)
        shown = fmt(k, val) if k != "ie" else f"1:{val:g}"
        if res.status != "ack":
            self.cmd = {"status": res.status, "what": f"{label} {shown}", "t": self.t}
            self.add_log("SYS", f"Ventilator {res.status.upper()} {label} -> {shown}", res.message or "")
            return
        frm = self.settings.get(k)
        self.settings[k] = val
        self.set_by[k] = {"src": src, "t": self.t}
        self.cmd = {"status": "ack", "what": f"{label} {shown}", "t": self.t}
        self.suggest.pop(k, None)
        frm_s = (fmt(k, frm) if k != "ie" else f"1:{frm:g}") if frm is not None else "?"
        if src == "CTRL":
            self.add_log("CTRL", f"{label} {frm_s} -> {shown}", "Acknowledged by the ventilator.")
        elif kind == "override" or kind == "emergency":
            self.overrides[k] = self.t + self.cfg["overrideMin"] * 60
            pre = "EMERGENCY O2: " if kind == "emergency" else "OVERRIDE "
            self.add_log("USER", f"{pre}{label} {frm_s} -> {shown}",
                         f"Operator took {label} from CLOVER. Controller will not adjust {label} for {self.cfg['overrideMin']} min unless released.")
        else:
            self.add_log("USER", f"{label} {frm_s} -> {shown}", "Manual adjustment by operator; sent to the ventilator.")

    # ------------------------------------------------------------ outbound
    def link_json(self) -> dict:
        return {
            "connected": self.connected, "stale": self.stale(),
            "lastFrameAge": None if self.last_frame_t is None else round(self.t - self.last_frame_t, 1),
            "cmd": self.cmd, "remote": self.remote, "mismatch": self.mismatch, "alarms": self.vent_alarms,
        }

    def frame_json(self) -> dict:
        return {
            "type": "frame", "t": round(self.t, 1),
            "spo2": self.filt["spo2"], "etco2": self.filt["etco2"], "shown": self.shown,
            "vent": self.settings, "measured": self.measured,
            "mode": self.mode, "engaged": self.engaged, "modeSince": self.mode_since, "runSince": self.run_since,
            "overrides": {k: round(u, 1) for k, u in self.overrides.items()},
            "suggest": self.suggest, "setBy": self.set_by, "targets": self.targets, "cfg": self.cfg,
            "link": self.link_json(),
            "nextDecisionIn": max(0.0, DECISION_PERIOD - (self.t - self.last_decision)) if self.engaged else None,
        }

    def snapshot_json(self) -> dict:
        d = self.frame_json()
        d.update({"type": "snapshot", "startT": int(self.start_wall * 1000),
                  "identity": None if self.identity is None else {
                      "make": self.identity.make, "model": self.identity.model, "serial": self.identity.serial,
                      "transport": self.identity.transport, "firmware": self.identity.firmware},
                  "hist": self.hist[-3600:], "log": [e.json() for e in self.log[-2000:]]})
        return d
