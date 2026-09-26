# CLOVER bridge

Connects the medic GUI to a ventilator (real, stub, or the Pulse Physiology Engine), runs
the controller, and keeps the log of record. See `CONTRACT.md` for the interfaces.

## The easy way: one launcher, two browser pages

Needs a Pulse runtime (see below). From the `clover` folder, double-click **CLOVER Demo.desktop** (Linux; the first time, KDE or
GNOME may ask you to trust or "allow launching" it), or `start_clover.py` on Windows, or run
`./start_clover.sh` from a terminal. On Linux file managers a `.sh` opens in the editor rather
than running, which is why the desktop launcher exists. The launcher always opens a terminal
window so you can see what it is doing, and appends to `clover_launcher.log`. It finds the Python with the Pulse bindings
(the sibling `vent_optimizer/.venv`), starts the bridge, and opens the **control page**:

- **http://localhost:8765/** — pick a patient (28 Pulse states), speed, initial vent
  settings; START / STOP the session; inject scenarios with parameters (ARDS severity,
  hemorrhage site and rate, pneumothorax side, fluids, probe off, stale stream, panel
  change, vent alarms); live status.
- **http://localhost:8765/clover** — the CLOVER medic display, already connected to the
  bridge (the control page has an OPEN CLOVER DISPLAY button). It shows LINK LOST until a
  session is started and reconnects on its own.

Close the launcher window (or Ctrl+C) to stop everything. No file paths or URL parameters
to type. Sessions can be restarted from the control page without restarting the launcher.

## Run with the simulated ventilator

```bash
pip install -r bridge/requirements.txt
python3 bridge/run_bridge.py --adapter stub --port 8765
```

Then open `gui/medic-console-prototype.html` with `?ws=ws://localhost:8765` appended to
its URL (for a `file://` page, append it to the path in the address bar). The ventilator
card's label reads **Ventilator · LIVE** and the model comes from the adapter.

## Run with the Pulse Physiology Engine as patient and ventilator

The bridge needs a **Pulse runtime**: a folder with `bin/` (the engine libraries, patient
states, substances, configs) and `python/pulse/` (the bindings). It looks, in order, at
`$CLOVER_PULSE_RUNTIME`, `<repo>/pulse_runtime/`, then `../vent_optimizer/pulse_engine`
(a full Pulse install). Build a trimmed runtime from a full install with:

```bash
python3 tools/make_pulse_runtime.py /path/to/pulse_engine pulse_runtime
```

That keeps ~100 MB of a 13 GB install (no verification data, test drivers, Java or
plotting subpackages) and writes `MANIFEST.json` with sizes and sha256 of every file.
`pulse_runtime/` is git-ignored: distribute it as an archive. Pulse is Apache 2.0; keep its
license and notices with the archive.

Python dependencies are in `requirements-pulse.txt` (protobuf pinned to the Pulse build's
major version, numpy, pandas, websockets). `start_clover.py` creates `.venv` and installs
them on first run. Manually:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-pulse.txt
.venv/bin/python bridge/run_bridge.py --adapter pulse --patient DefaultMale
.venv/bin/python bridge/run_bridge.py --adapter pulse --patient CSTARS-Patient3 --speed 5
```

`--patient` is any state in the runtime's `bin/states` (28 of them), or a path. `--speed`
runs physiology faster than real time for demos; 1.0 is real time.

What Pulse gives the GUI that the stub cannot: a real capnogram (CO₂ at the carina, 50 Hz),
a pulse waveform (arterial pressure standing in for the pleth), the ventilator model's own
VTe/PIP/Pplat/Pmean, and disturbances with real physiology. The SIM panel's Pulse buttons
(hemorrhage, airway obstruction, tension pneumothorax, needle decompression) work only
with this adapter; the shared ones (lung injury, CO₂ rise, probe off, link faults) work
with both. Out-of-range writes are rejected the way a real vent would reject them.

Pulse patients load awake and un-intubated. The adapter places a tracheal tube and abolishes
spontaneous drive by default (Pulse dyspnea severity 1.0), which gives clean controlled
ventilation. The control page lets you choose not intubated, reduced or full effort, and CMV
or AC, and two scenarios ("Patient wakes up", "Sedate and paralyse") switch drive on and off
mid-session to show asynchrony deliberately. Setting changes are applied at end-expiration
because any mid-breath reconfiguration restarts Pulse's breath cycle.

## Run with your ventilator

1. Copy `clover_bridge/stub_adapter.py` to a private module and implement the five
   `VentAdapter` methods against your serial protocol. Keep it out of the public repo.
2. `python3 bridge/run_bridge.py --adapter your_module:YourAdapter`
3. Start in MANUAL for the first bench run (send `{"type":"cmd","name":"mode","mode":"MANUAL"}`
   or tap SWITCH TO MANUAL) so the controller advises but writes nothing, then switch to
   AUTO with a test lung attached.

## Tests

Standard library only, no hardware, no network:

```bash
python3 -m unittest discover -s bridge/tests -v
```

## Layout

```
bridge/
  run_bridge.py            entry point
  requirements.txt         websockets, pyserial (network/serial layers only)
  CONTRACT.md              GUI <-> bridge <-> ventilator interfaces
  clover_bridge/
    adapter.py             VentAdapter interface, Frame / WaveChunk / Alarm / CommandResult
    stub_adapter.py        simulated ventilator + patient (demo, tests)
    pulse_adapter.py       Pulse Physiology Engine as patient + ventilator
    pulse_support/         runtime lookup (pulse_env.py) and disturbance vocabulary (actions.py), vendored
    controller.py          CLOVER decision logic with reasoning strings
    session.py             session state, authority model, log, frame/snapshot JSON
    server.py              asyncio WebSocket server, sessions, admin commands
    webui.py               serves the control page and the CLOVER display over HTTP
  control.html             the Pulse control page
    openice.py             OpenICE/DDS topic mapping and publisher hook (optional)
  tests/test_core.py         stdlib unit tests
  tests/test_integration.py  bridge + stub over a real WebSocket (needs websockets)
  tests/test_pulse.py        Pulse adapter (needs a Pulse runtime and requirements-pulse.txt)
tools/make_pulse_runtime.py  build a trimmed pulse_runtime/ from a full Pulse install
requirements-pulse.txt       Python deps for Pulse + bridge
```
