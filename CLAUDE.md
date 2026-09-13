# CLOVER

Closed Loop Oxygenation and Ventilation for Resuscitation: a closed-loop controller that
reads SpO₂ and etCO₂ and adjusts VT, RR, PEEP and FiO₂ on a ventilator. The controller is
treated as complete. This repo holds the GUI work.

## Read first

`gui/DESIGN.md` records every design rule and decision for the medic console. Follow it
for any GUI change or new screen. The hard rules in its section 2 are non-negotiable:
fixed-size boxes, no jittering digits, two-tap confirmation for state changes, trends only
on request, everything logged with reasoning, one strict grid.

## Files

- `gui/medic-console-prototype.html` — the 68W medic screen. Standalone, fonts embedded,
  works offline. Contains a simulated patient and a copy of the controller logic so the
  demo is self-running. The dashed SIM button (bottom-left card) is demo scaffolding only.
- `gui/DESIGN.md` — design rules, palette, layout numbers, vocabulary, controller
  interaction rules, and what's not yet built.
- `bridge/` — Python bridge between the GUI (WebSocket) and a ventilator (serial): the
  `VentAdapter` interface, a stub ventilator, the controller, session/authority model, and
  the log of record. `bridge/CONTRACT.md` is the interface spec. Tests are stdlib-only:
  `python3 -m unittest discover -s bridge/tests`. The vendor adapter is private and lives
  outside this repo. `pulse_adapter.py` runs the Pulse Physiology Engine (from the sibling
  `../vent_optimizer` project, use its `.venv`) as patient and ventilator for demos.

## Live mode

Open the GUI with `?ws=ws://host:port` to drive it from the bridge instead of the built-in
simulator. In live mode the GUI never applies a command locally; it sends it and renders
the next frame. Keep that rule when adding controls.

## Working conventions

- The prototype is a single HTML file, no build step, no libraries. Keep it that way.
- The stage is a fixed 1280×990 grid scaled to the window. All row heights are explicit
  pixels; never let a row be "the remainder".
- Vocabulary: FULL AUTO / PARTIAL OVERRIDE / MANUAL / DISENGAGED, ENGAGE / DISENGAGE CLOVER, UPDATE, SETUP, BRIGHT / DIM / RED. Never STOP, START, HALTED, HYBRID.
- After editing, re-check that left-column and right-column row edges still align.
