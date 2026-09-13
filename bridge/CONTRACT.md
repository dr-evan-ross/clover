# CLOVER interface contract

Two boundaries, one process between them.

```
 GUI (browser)  <== WebSocket, JSON ==>  bridge (Python)  <== serial ==>  ventilator
                                             |
                                             +-- controller (decisions + reasoning)
                                             +-- optional OpenICE/DDS publisher
```

The GUI never talks to the ventilator. The bridge owns the session: time, mode,
overrides, targets, the log of record, and the only path to the vent. The GUI is a
display and command surface; in live mode it sends commands and renders frames.

Open the GUI with `?ws=ws://host:port` to use a bridge instead of the built-in simulator.

---

## 1. Ventilator side: `VentAdapter` (Python, `clover_bridge/adapter.py`)

Implement one class per ventilator. Vendor-specific code stays in that class. Two
implementations ship: `stub_adapter.py` (hand-written model) and `pulse_adapter.py` (the Pulse
Physiology Engine as patient and ventilator, via the sibling `vent_optimizer` project).

| Method | Purpose |
|---|---|
| `start() -> Identity` | open transport, handshake, return make/model/serial/firmware/transport and capabilities |
| `stop()` | close |
| `frames() -> async iter Frame` | ~1 Hz snapshots: `settings` (the vent's own read-back), `measured` (open dict), `remote`, `alarms`, `seq`, `ts` |
| `waves() -> async iter WaveChunk` | waveform bursts at any rate: `key` (`pleth`, `capno`, …), `rate`, `samples` |
| `set_setting(key, value) -> CommandResult` | write one of `vt rr peep fio2 ie`; returns `ack` / `rejected` / `timeout` / `unsupported` |

Design points that came from the product owner:

- **Reads are richer than the GUI needs and will grow.** `Frame.measured` is an open dict;
  known keys are `spo2 etco2 mve vte leak ppeak pplat`. Unknown keys pass through to the
  GUI frame under `measured` and are ignored until a tile wants them.
- **Waveforms are faster than 1 Hz.** Each `WaveChunk` declares its own rate. Pleth is
  normalised 0..1; capnogram is in mmHg.
- **Writes cover every GUI setting.** If the device gives no explicit acknowledgement,
  return `ack` only once the read-back matches; the bridge treats read-back as truth anyway.
  Apply changes breath-synchronously where the device allows it: the Pulse adapter queues a
  change and applies it at end-expiration, because any mid-breath reconfiguration restarts
  Pulse's breath cycle (measured: an 869 mL merged breath and a 6 s gap). While queued the
  GUI shows the command as PENDING; `ack` arrives when it has actually taken effect.
- **Remote-control state is optional.** `Frame.remote` is `granted` / `locked` / `local` /
  `None`. `None` shows as NOT REPORTED on the GUI; local panel changes are then inferred
  from read-back mismatches.
- **Alarms: any number, any names.** `Frame.alarms` is a list of `Alarm(id, text, severity)`.
  They reach the GUI's alert band prefixed `VENT ·`.

Staleness is the bridge's job: no frame for 3 s → STALE (controller holds, controls lock);
no frame for 30 s → LINK LOST.

---

## 2. GUI side: WebSocket messages (JSON)

### Bridge → GUI

`snapshot` (once, on connect) = a `frame` plus:

```json
{ "type":"snapshot", "startT": 1757000000000, "identity": {"make":"…","model":"…","serial":"…","transport":"…"},
  "hist": [ {"t":0.0,"spo2":96.1,"etco2":40.2}, … ], "log": [ {"t":0.0,"src":"SYS","title":"…","detail":"…"}, … ] }
```

`frame` (every bridge tick, 1 Hz):

```json
{ "type":"frame", "t": 1234.0,
  "spo2": 95.8, "etco2": 40.3, "shown": {"spo2":96,"etco2":40},
  "vent": {"vt":500,"rr":14,"peep":5,"fio2":0.4,"ie":2.0},
  "measured": {"mve":6.7,"vte":480,"leak":4,"ppeak":22,"pplat":14, "…":"vendor extras pass through"},
  "mode":"AUTO", "engaged":true, "modeSince":0.0, "runSince":0.0,
  "overrides": {"fio2": 1500.0},            "//": "key -> session time the override ends",
  "suggest": {"rr":16},                       "//": "MANUAL advisories",
  "setBy": {"fio2":{"src":"USER","t":1200.0}},
  "targets": {"spo2":[92,96],"etco2":[35,45]}, "cfg": {"overrideMin":5},
  "link": { "connected":true, "stale":false, "lastFrameAge":0.4,
            "cmd": {"status":"ack","what":"FiO2 50%","t":1200.0},   "//": "ack|pending|rejected|timeout|unsupported|none",
            "remote":"granted",                                     "//": "granted|locked|local|null",
            "mismatch": null,                                       "//": "or {k, val, at}",
            "alarms": [ {"id":"hp","txt":"HIGH PRESSURE","sev":"crit"} ] },
  "nextDecisionIn": 17.0 }
```

`wave`: `{ "type":"wave", "k":"spo2"|"etco2", "rate":25, "samples":[…] }` (pleth 0..1, capno mmHg)

`log`: `{ "type":"log", "entry": {"t":…, "src":"CTRL|USER|SYS|VENT", "title":"…", "detail":"…"} }`

Times: `t` is seconds since the bridge session started; `startT` is that origin in epoch ms.
The GUI displays `startT + t`, in LOCAL or ZULU as the operator chooses.

### GUI → Bridge

All commands are `{"type":"cmd", "name": …}`:

| name | fields | effect |
|---|---|---|
| `set` | `k`, `value` | write a setting; in AUTO this starts an override of `cfg.overrideMin` |
| `release` | `k` | end an override early; controller decides immediately |
| `mode` | `mode`: `AUTO`/`MANUAL` | switch; entering MANUAL clears overrides |
| `engage` | `engaged`: bool | DISENGAGE releases the vent; ENGAGE reads back and starts FULL AUTO |
| `update` | — | force a controller decision now |
| `target` | `k`, `lo`, `hi` | change a target range |
| `ie` | `idx` | I:E from the fixed list `1:1 1:1.5 1:2 1:2.5 1:3 1:4` |
| `cfg` | `overrideMin` | SETUP: override lockout in minutes |
| `o2_emergency` | — | FiO₂ 100%, counts as an override |
| `log` | `title`, `detail` | record a UI-only event in the log of record |

`{"type":"scenario","name":…}` is demo scaffolding: forwarded to the adapter's `scenario()`
if it has one (the stub does; a real adapter should not).

The GUI never applies a command locally in live mode. It sends it, and the next `frame`
shows the result. This keeps "what the vent is doing" and "what the operator asked for"
from ever being confused on screen.

---

## 3. Authority model (shared by both sides)

| State | Who may change a setting | GUI shows |
|---|---|---|
| FULL AUTO | controller | green CLOVER IN CONTROL on every tile |
| PARTIAL OVERRIDE | controller, except overridden keys (operator) | mixed bands; mode tile amber with pips |
| MANUAL | operator; controller advises | amber USER IN CONTROL; SUGGESTS chips |
| DISENGAGED | nobody via this screen; vent's own panel | grey VENT PANEL IN CONTROL |

A read-back mismatch that persists 5 s means the vent's panel was used: the bridge adopts
the vent's value and, in AUTO, treats it as an operator override.

---

## 4. OpenICE

Optional. See `clover_bridge/openice.py` for the topic mapping (DeviceIdentity,
DeviceConnectivity, Numeric with 11073 metric IDs, SampleArray for waveforms) and the two
custom topics (`clover/Decision`, `clover/Command`). Writes to the ventilator never go over
DDS; the adapter owns the serial link.
