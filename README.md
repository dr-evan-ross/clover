# CLOVER

Closed Loop Oxygenation and Ventilation for Resuscitation.

A physiological closed-loop controller that reads SpO₂ and etCO₂ and adjusts VT, RR, PEEP and FiO₂ on a mechanical ventilator. This repository holds the GUI work.

## Contents

- `gui/medic-console-prototype.html` — the 68W combat medic console. Standalone HTML, fonts embedded, works offline. Open it in any modern browser; it includes a simulated patient and controller so the demo runs itself.
- `gui/DESIGN.md` — design rules, palette, layout, vocabulary and controller interaction decisions.

- `bridge/` — Python bridge to a real ventilator (WebSocket to the GUI, serial to the vent),
  with the controller, a simulated ventilator, and `CONTRACT.md` describing the interfaces.

## Status

Medic screen complete as a prototype; bridge skeleton and interface contract in place with a stub ventilator (14 stdlib tests). Next: the vendor adapter (private), a bench run on a mannequin in MANUAL, then AUTO on a test lung. Provider/RT view and custom layout follow.
