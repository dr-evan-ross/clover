"""VentAdapter: the one interface a ventilator must be wrapped in.

Design constraints this encodes (from the product owner):
  * reads stream at ~1 Hz; waveforms arrive faster (rate declared per waveform)
  * the read set is richer than the GUI needs and will grow -> frames are open dicts
  * writes cover every setting the GUI exposes (VT, RR, PEEP, FiO2, I:E)
  * remote-control state may or may not be reported -> optional, tri-state
  * alarms: any number, any names -> a list of Alarm records
Keep vendor-specific code in a private module implementing this class; nothing
else in the bridge should know which ventilator it is talking to.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Optional

# Canonical setting keys used across GUI, controller and adapters.
SETTINGS = ("vt", "rr", "peep", "fio2", "ie")
# Canonical measurement keys the GUI shows. Adapters may report more; extras pass through.
MEASURED = ("spo2", "etco2", "mve", "vte", "leak", "ppeak", "pplat")


@dataclass
class Identity:
    make: str
    model: str
    serial: str
    firmware: str = ""
    transport: str = "serial"          # e.g. "serial 115200 8N1", "tcp", ...
    supports_remote_state: bool = False  # does it report panel-lock / local activity?
    writable: tuple[str, ...] = SETTINGS
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Frame:
    """One ~1 Hz snapshot from the ventilator.

    settings: the ventilator's *own* report of its current settings (read-back).
    measured: measurements keyed by MEASURED names plus anything extra.
    remote:   'granted' | 'locked' | 'local' | None (None = not reported)
    alarms:   list of Alarm currently active on the ventilator
    seq/ts:   sequence number and device timestamp if the protocol has them;
              the bridge uses them (or arrival time) to detect a stale stream.
    """
    settings: dict[str, float]
    measured: dict[str, float]
    remote: Optional[str] = None
    alarms: list["Alarm"] = field(default_factory=list)
    seq: Optional[int] = None
    ts: Optional[float] = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class WaveChunk:
    """A burst of waveform samples. rate is samples/s; samples are in the units
    the GUI expects: pleth normalised 0..1, capnogram in mmHg."""
    key: str                 # "pleth" | "capno" | vendor-specific extras
    rate: float
    samples: list[float]
    ts: Optional[float] = None


@dataclass
class Alarm:
    id: str
    text: str
    severity: str            # "info" | "warn" | "crit"
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class CommandResult:
    status: str              # "ack" | "rejected" | "timeout" | "unsupported"
    message: str = ""
    readback: Optional[float] = None


class VentAdapter:
    """Implement these five methods for a real ventilator."""

    async def start(self) -> Identity:
        """Open the transport, handshake, return the device identity."""
        raise NotImplementedError

    async def stop(self) -> None:
        raise NotImplementedError

    def frames(self) -> AsyncIterator[Frame]:
        """Async iterator of ~1 Hz Frames. Should not raise on a gap; the bridge
        detects staleness by arrival time."""
        raise NotImplementedError

    def waves(self) -> AsyncIterator[WaveChunk]:
        """Async iterator of waveform chunks, any rate. May yield nothing if the
        device has no waveform output."""
        raise NotImplementedError

    async def set_setting(self, key: str, value: float) -> CommandResult:
        """Write one setting. Return ack/rejected/timeout. If the device gives no
        explicit acknowledgement, return 'ack' only after the read-back matches."""
        raise NotImplementedError


class AdapterError(Exception):
    pass


async def aiter_from_queue(q: "asyncio.Queue") -> AsyncIterator[Any]:
    """Helper for adapters that push into a queue from a reader task."""
    while True:
        yield await q.get()
