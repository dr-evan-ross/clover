"""OpenICE / DDS publishing hook (optional, not wired by default).

OpenICE (MD PnP Lab, ASTM F2761 "Integrated Clinical Environment") carries
device data over DDS in a fixed set of topics. The bridge's Session maps onto
them as follows; implement `OpenICEPublisher` against your DDS vendor
(RTI Connext via `rticonnextdds-connector`, or Eclipse Cyclone DDS) when you
want the oximeter/capnograph adapters that already exist for OpenICE to join
the same data space, or want OpenICE's supervisor tooling to see CLOVER.

  Session field              OpenICE topic / field                    Notes
  ------------------------   --------------------------------------   -------------------------------
  identity                   DeviceIdentity (unique_device_identifier, manufacturer, model, serial_number)
  connected / stale          DeviceConnectivity (state)               Connected | Disconnected; stale is a CLOVER notion
  filt.spo2                  Numeric  metric_id=MDC_PULS_OXIM_SAT_O2  unit MDC_DIM_PERCENT
  filt.etco2                 Numeric  metric_id=MDC_AWAY_CO2_ET       unit MDC_DIM_MMHG
  settings.rr                Numeric  MDC_VENT_RESP_RATE (setting)     setting vs measured is a separate field/instance
  settings.vt                Numeric  MDC_VOL_AWAY_TIDAL (setting)
  settings.peep              Numeric  MDC_PRESS_AWAY_END_EXP_POS
  settings.fio2              Numeric  MDC_CONC_AWAY_O2 / MDC_VENT_CONC_AWAY_O2_INSP
  measured.ppeak/pplat       Numeric  MDC_PRESS_AWAY_INSP_MAX / MDC_PRESS_AWAY_PLATEAU
  measured.mve/vte           Numeric  MDC_VOL_MINUTE_AWAY_EXP / MDC_VOL_AWAY_TIDAL_EXP
  pleth wave                 SampleArray metric_id=MDC_PULS_OXIM_PLETH  frequency = chunk rate
  capnogram wave             SampleArray metric_id=MDC_AWAY_CO2           frequency = chunk rate
  vent_alarms                AlarmSettings / (device alarm topics)      pass through, text preserved
  controller decisions       custom topic  clover/Decision              {t, mode, changes[], reasoning}
  GUI commands (writes)      custom topic  clover/Command               {t, key, value, source, kind, result}

Nomenclature codes are ISO/IEEE 11073-10101; confirm exact IDs against the
OpenICE IDL for the version you deploy. Writes to the ventilator do NOT go
over DDS in this design: the adapter owns the vent's serial link, and CLOVER's
commands are published on clover/Command for observability only.
"""
from __future__ import annotations

from typing import Protocol

from .session import Session


class OpenICEPublisher(Protocol):
    def publish_identity(self, s: Session) -> None: ...
    def publish_numerics(self, s: Session) -> None: ...
    def publish_wave(self, key: str, rate: float, samples: list[float]) -> None: ...
    def publish_decision(self, s: Session, reasoning: str, changes: list) -> None: ...


class NullPublisher:
    """Default: publish nothing. Swap in a real DDS publisher via server wiring."""
    def publish_identity(self, s: Session) -> None: pass
    def publish_numerics(self, s: Session) -> None: pass
    def publish_wave(self, key: str, rate: float, samples: list[float]) -> None: pass
    def publish_decision(self, s: Session, reasoning: str, changes: list) -> None: pass
