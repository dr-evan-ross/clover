# CLOVER

Closed Loop Oxygenation and Ventilation for Resuscitation.

A physiological closed-loop controller that reads SpO₂ and etCO₂ and adjusts VT, RR, PEEP and FiO₂ on a mechanical ventilator. This repository holds the GUI work.

## Contents

- `gui/medic-console-prototype.html` — the 68W combat medic console. Standalone HTML, fonts embedded, works offline. Open it in any modern browser; it includes a simulated patient and controller so the demo runs itself.
- `gui/DESIGN.md` — design rules, palette, layout, vocabulary and controller interaction decisions.

## Status

Medic screen complete as a prototype. Provider/RT view, custom layout mode, and the interface to the real controller are next.
