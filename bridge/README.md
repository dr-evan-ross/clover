# CLOVER bridge

Connects the medic GUI to a real ventilator, runs the controller, and keeps the log of
record. See `CONTRACT.md` for the interfaces.

## Run with the simulated ventilator

```bash
pip install -r bridge/requirements.txt
python3 bridge/run_bridge.py --adapter stub --port 8765
```

Then open `gui/medic-console-prototype.html` with `?ws=ws://localhost:8765` appended to
its URL (for a `file://` page, append it to the path in the address bar). The ventilator
card's label reads **Ventilator · LIVE** and the model comes from the adapter.

## Run with the Pulse Physiology Engine as patient and ventilator

Uses the Pulse build and helpers in the sibling `vent_optimizer` project, so run under
that project's Python (which has the Pulse bindings; `websockets` is installed there too):

```bash
../vent_optimizer/.venv/bin/python bridge/run_bridge.py --adapter pulse --patient DefaultMale
../vent_optimizer/.venv/bin/python bridge/run_bridge.py --adapter pulse --patient CSTARS-Patient3 --speed 5
```

`--patient` is any state in `vent_optimizer/pulse_engine/bin/states` (28 of them), or a path.
`--speed` runs physiology faster than real time for demos; 1.0 is real time. Set
`CLOVER_VENT_OPTIMIZER` or `--vent-optimizer` if the project is not at `../vent_optimizer`.

What Pulse gives the GUI that the stub cannot: a real capnogram (CO₂ at the carina, 50 Hz),
a pulse waveform (arterial pressure standing in for the pleth), the ventilator model's own
VTe/PIP/Pplat/Pmean, and disturbances with real physiology. The SIM panel's Pulse buttons
(hemorrhage, airway obstruction, tension pneumothorax, needle decompression) work only
with this adapter; the shared ones (lung injury, CO₂ rise, probe off, link faults) work
with both. Out-of-range writes are rejected the way a real vent would reject them.

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
    pulse_adapter.py       Pulse Physiology Engine as patient + ventilator (needs vent_optimizer)
    controller.py          CLOVER decision logic with reasoning strings
    session.py             session state, authority model, log, frame/snapshot JSON
    server.py              asyncio WebSocket server tying it together
    openice.py             OpenICE/DDS topic mapping and publisher hook (optional)
  tests/test_core.py         stdlib unit tests
  tests/test_integration.py  bridge + stub over a real WebSocket (needs websockets)
  tests/test_pulse.py        Pulse adapter (needs vent_optimizer; run under its .venv)
```
