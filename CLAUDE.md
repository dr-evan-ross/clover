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

## Working conventions

- The prototype is a single HTML file, no build step, no libraries. Keep it that way.
- The stage is a fixed 1280×990 grid scaled to the window. All row heights are explicit
  pixels; never let a row be "the remainder".
- Vocabulary: AUTO / MANUAL / STOPPED, START / STOP, UPDATE, BRIGHT / DIM / RED.
- After editing, re-check that left-column and right-column row edges still align.
