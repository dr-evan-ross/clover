"""A simulated ventilator + patient implementing VentAdapter.

Used for tests and for demos without hardware. It mirrors the GUI prototype's
built-in simulator, and exposes scenario hooks (desaturation, CO2 rise, probe
off, stale stream, reject next command, panel change, vent alarm) so every
state the GUI can show is reachable from the bridge too.
"""
from __future__ import annotations

import asyncio
import math
import random
import time
from typing import AsyncIterator

from .adapter import (Alarm, CommandResult, Frame, Identity, VentAdapter,
                      WaveChunk)


class StubVent(VentAdapter):
    def __init__(self, speed: float = 1.0, seed: int | None = 1):
        self.speed = speed
        self.rng = random.Random(seed)
        self.settings = {"vt": 500.0, "rr": 14.0, "peep": 5.0, "fio2": 0.40, "ie": 2.0}
        self.patient = {"shunt": 0.12, "vco2": 200.0, "signal": True}
        self.spo2 = 96.0
        self.etco2 = 40.0
        self.seq = 0
        self.alarms: list[Alarm] = []
        self.remote = "granted"
        self._remote_until = 0.0
        self._stale_until = 0.0
        self._reject_next = False
        self._stop = asyncio.Event()
        self.t = 0.0  # simulated seconds

    # ---- VentAdapter ----
    async def start(self) -> Identity:
        return Identity(make="SIM", model="Stub ventilator", serial="SIM-0001",
                        transport="in-process", supports_remote_state=True,
                        writable=("vt", "rr", "peep", "fio2", "ie"))

    async def stop(self) -> None:
        self._stop.set()

    async def frames(self) -> AsyncIterator[Frame]:
        while not self._stop.is_set():
            await asyncio.sleep(1.0 / self.speed)
            self._step(1.0)
            if self.t < self._stale_until:
                continue  # stream stalled: emit nothing
            yield self.frame()

    async def waves(self) -> AsyncIterator[WaveChunk]:
        rate, chunk = 25.0, 0.2
        while not self._stop.is_set():
            await asyncio.sleep(chunk / self.speed)
            if self.t < self._stale_until:
                continue
            n = int(rate * chunk)
            t0 = time.monotonic()
            yield WaveChunk("pleth", rate, [self._pleth(t0 + i / rate) for i in range(n)])
            yield WaveChunk("capno", rate, [self._capno(t0 + i / rate) for i in range(n)])

    async def set_setting(self, key: str, value: float) -> CommandResult:
        if key not in self.settings:
            return CommandResult("unsupported", f"{key} is not a writable setting")
        await asyncio.sleep(0.3 / self.speed)  # command round trip
        if self._reject_next:
            self._reject_next = False
            return CommandResult("rejected", "out of device range", readback=self.settings[key])
        self.settings[key] = float(value)
        return CommandResult("ack", "", readback=self.settings[key])

    # ---- scenario hooks (not part of the interface) ----
    def scenario(self, name: str) -> None:
        p = self.patient
        if name == "injury":
            p["shunt"] = 0.45
        elif name == "hypo":
            p["vco2"] = 300.0
        elif name == "recover":
            p["shunt"], p["vco2"] = 0.12, 200.0
        elif name == "dropout":
            p["signal"] = False
            self._signal_until = self.t + 40
        elif name == "stale":
            self._stale_until = self.t + 15
        elif name == "reject":
            self._reject_next = True
        elif name == "panel":
            self.settings["vt"] = 550.0
            self.remote, self._remote_until = "local", self.t + 20
        elif name == "alarm":
            self.alarms.append(Alarm("hp", "HIGH PRESSURE", "crit"))
            self._alarm_until = self.t + 30
        else:
            raise ValueError(name)

    # ---- model ----
    def _step(self, dt: float) -> None:
        self.t += dt
        v, p = self.settings, self.patient
        oi = (v["fio2"] - 0.21) * 100 + v["peep"] * 2.5 - p["shunt"] * 120
        spo2_ss = max(55.0, min(100.0, 100 - 30 / (1 + math.exp((oi + 2) / 5))))
        mv_alv = max(0.3, v["rr"] * (v["vt"] - 150) / 1000)
        et_ss = max(8.0, min(110.0, p["vco2"] / mv_alv))
        self.spo2 += (spo2_ss - self.spo2) / 45 * dt + (self.rng.random() - 0.5) * 0.25
        self.etco2 += (et_ss - self.etco2) / 90 * dt + (self.rng.random() - 0.5) * 0.4
        if not p["signal"] and self.t >= getattr(self, "_signal_until", 0):
            p["signal"] = True
        if self.remote == "local" and self.t >= self._remote_until:
            self.remote = "granted"
        if self.alarms and self.t >= getattr(self, "_alarm_until", 0):
            self.alarms = []

    def frame(self) -> Frame:
        self.seq += 1
        v = self.settings
        leak = 4.0
        vte = round(v["vt"] * (1 - leak / 100) / 5) * 5
        measured = {
            "spo2": self.spo2 if self.patient["signal"] else None,
            "etco2": self.etco2,
            "mve": round(v["rr"] * vte / 1000, 1),
            "vte": vte,
            "leak": leak,
            "ppeak": round(v["peep"] + v["vt"] / 28 + 2),
            "pplat": round(v["peep"] + v["vt"] / 40 + 1),
            "battery_pct": 84, "o2_supply_psi": 1640,  # examples of "richer than we need"
        }
        return Frame(settings=dict(v), measured=measured, remote=self.remote,
                     alarms=list(self.alarms), seq=self.seq, ts=time.time())

    def _pleth(self, t: float) -> float:
        if not self.patient["signal"]:
            return 0.5 + (self.rng.random() - 0.5) * 0.06
        hr = max(60.0, min(140.0, 70 + (96 - self.spo2) * 3))
        ph = (t * hr / 60) % 1
        perf = max(0.35, min(1.0, (self.spo2 - 70) / 30))
        y = math.exp(-((ph - 0.12) / 0.07) ** 2) + 0.5 * math.exp(-((ph - 0.42) / 0.14) ** 2)
        return 0.12 + 0.78 * perf * max(0.0, min(1.0, y))

    def _capno(self, t: float) -> float:
        T = 60 / self.settings["rr"]
        ph = (t / T) % 1
        et = self.etco2
        if ph < 0.05:
            y = et * 0.92 * (ph / 0.05)
        elif ph < 0.667:
            y = et * (0.92 + 0.08 * (ph - 0.05) / 0.617)
        elif ph < 0.70:
            y = et * (1 - (ph - 0.667) / 0.033)
        else:
            y = 0.3
        return max(0.0, y)
