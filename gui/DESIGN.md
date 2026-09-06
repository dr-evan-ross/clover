# CLOVER GUI — Design Rules and Decisions

CLOVER (Closed Loop Oxygenation and Ventilation for Resuscitation) is a physiological
closed-loop controller. It reads SpO₂ and etCO₂, decides what to change on the ventilator
(VT, RR, PEEP, FiO₂), and sends those commands. The controller is treated as complete;
this folder is the GUI work.

This file records every design decision made for the first screen, the **68W combat medic
console**, so later screens (provider/RT view, custom layout) inherit them without
re-deriving anything. Decisions were made with the product owner on 2026-09-05 and 06.

Prototype: `gui/medic-console-prototype.html` (standalone, fonts embedded, works offline,
contains a simulated patient and a copy of the controller logic).
Live copy: https://claude.ai/code/artifact/36cc1857-265e-48fb-bb69-dfe3d6dfe321

---

## 1. Who it's for, and the governing principle

The default screen targets a 68W combat medic under stress, on a touchscreen no larger
than a sheet of paper, possibly at night. The owner's words: *"I'm trying not to overwhelm
my joes."* Every rule below serves that. When in doubt, remove, hide behind a tap, or hold
still.

## 2. Hard rules (do not break these on any screen)

1. **Nothing moves unless the patient moved.** Every box is a fixed size regardless of
   its text or state. Space for conditional controls (APPLY/CANCEL, suggestion chips,
   release buttons, confirm buttons) is permanently reserved and only made visible.
2. **Digits never jitter.** Every changing number renders one character per fixed-width
   cell (the display font has no tabular figures). Vital displays are also smoothed: a
   ~5 s moving average, and the shown integer only changes once the average has moved
   0.75 past it.
3. **No single touch changes anything.** START/STOP, AUTO/MANUAL and emergency FiO₂ all
   arm on the first tap and act on a second tap within 5 s (8 s for O₂). Ventilator
   adjustments are pending until APPLY, and discard themselves after 12 s.
   Exception: UPDATE (force a controller decision) is single-tap, because the controller
   would take the same action within 30 s anyway.
4. **Waveforms are always visible.** Each vital tile shows its live waveform (pleth under
   SpO₂, capnogram under etCO₂) beneath the number, as a sweeping trace with an erase bar
   like a bedside monitor, ~12 s across the tile. Assumed FDA requirement: a number is
   only trustworthy if its waveform is. Signal loss shows a noisy flat pleth; ventilator
   link loss shows an absent capnogram. This is patient movement and does not break rule 1.
5. **Trends are hidden until asked for.** No sparklines, no inline trend arrows. A TREND
   button in each vital tile swaps the number and waveform for a chart of the same
   footprint (15 / 30 / 60 min). Never a full-screen overlay; the rest of the screen stays visible.
6. **Everything the controller does is logged with its reasoning.** So is every user
   action, every alert onset and clearance, and every armed-but-not-confirmed tap.
7. **Who is in charge of each setting is always explicit.** Every control tile carries an
   ownership band across its top and a matching colour scheme: green "CLOVER IN CONTROL"
   when the controller may change it (AUTO, running, no override); amber "USER IN CONTROL" (with
   the override countdown or "· STOPPED" appended) when
   only the operator can (MANUAL, an active override with its countdown, or STOPPED).
   Who set the value *last* is deliberately not shown on the tile, only the set time,
   because it is easily confused with who is in charge *now*. The log has the history.
8. **One strict grid.** The top bar is split into boxes matching the columns beneath.
   Left-column row heights equal right-column row heights exactly. All row heights are
   explicit pixels; nothing is "the remainder".

## 3. Vocabulary

| Use | Never |
|---|---|
| AUTO, MANUAL, STOPPED | AUTOMATIC, MANUAL MODE, HALTED |
| START, STOP | GO |
| UPDATE (force a decision) | DECIDE NOW |
| BRIGHT, DIM, RED (display) | DAY, LIGHT |
| RETURN TO CLOVER (release override) | — |
| Units only on control tiles: %, cmH₂O, mL, /min | prefixes like "O₂ ·" or "CO₂ ·" |

## 4. Colour

Tactical low-light palette, single theme (always dark).

| Token | Hex | Use |
|---|---|---|
| ground | `#080b07` | page |
| panel | `#0f140d` | tiles |
| raised | `#161d13` / `#1f281b` / `#2a3625` | buttons, chips |
| line | `#2a3625` / `#3a4834` | borders |
| ink | `#d3dac6` / `#8d9a7e` / `#5b6850` | text, secondary, muted |
| go / auto | `#5f9e3b` | AUTO state, START, connected, in-range band |
| warn | `#d5a02c` | MANUAL state, out-of-range fill, APPLY, overrides |
| crit / stop | `#d84b38` / `#b7402c` | STOPPED, critical fill, STOP button, CONFIRM |
| info | `#6d9ab8` | informational alerts, signal loss |
| SpO₂ identity | `#7fc4e6` (cyan) | SpO₂ number/label, FiO₂ and PEEP names, SpO₂ trend line |
| etCO₂ identity | `#f0dc7a` (yellow) | etCO₂ number/label, VT and RR names, etCO₂ trend line |

Rules:
- Identity colours are constant. **The number never changes colour with state.**
- State is shown by the **whole tile filling**: amber `#4b3309` for out of range, red
  `#5e1b11` pulsing for critical, blue `#132433` dashed for signal lost. A corner pill
  (OUT OF RANGE / CRITICAL / NO SIGNAL) carries the state in words too.
- Armed controls share one smooth pulse: brightness 1 → 1.35 and border fading to white
  with a soft halo, 1.2 s ease-in-out. Never a stepped blink.
- Display modes: BRIGHT (normal), DIM (55% black overlay), RED (red multiply overlay,
  desaturated). Only RED is tinted on the DISPLAY button.

## 5. Type

- Display / numerics / headings: **Chakra Petch** 500–700
- UI text: **Barlow** 400–700
- Log and timestamps: **IBM Plex Mono** 400–500
- All three are embedded in the standalone file as WOFF2 data URIs (SIL OFL).

## 6. Layout (1280 × 990 stage, scaled to fit; letter-landscape proportions)

Top bar (76 px), four boxes: ventilator (268) · controller (476) · DISPLAY (231) · clock (231).
The controller box holds the status text, a RUN TIME box, and the UPDATE countdown button. The clock is a button that toggles
LOCAL / ZULU; every displayed time, including the log, follows it.

Left column (268 px), rows **726 / 130**:
1. **Closed-loop tile** (one button, spanning the vitals, summary and controls rows). Its
   upper zone (432, beside vitals + summary) is the status: "Closed loop" label · loop icon
   · AUTO/MANUAL/STOPPED · since-time · description. Its lower zone (280, beside the
   controls it governs, separated by a rule on the row line) is the switch: SWITCH TO
   MANUAL / SWITCH TO AUTO with the action word in the *target* mode's colour; tapping
   anywhere on the tile arms it, second tap confirms; inert when STOPPED. The tile is the
   state and the control in one place, which is why it is not split.
2. **START / STOP** button (beside alerts).

The ventilator connection (CONNECTED / LINK LOST, battery, make and model, link type)
lives in the top-left box of the top bar, above this column, together with the demo-only
SIM button.

Right column, rows **354 / 64 / 280 / 130**:
1. **SpO₂** and **etCO₂** tiles (target range button, big number, a 90 px waveform strip
   beneath it, TREND button bottom-right;
   SpO₂ also has FiO₂ → 100% + CONFIRM bottom-left). Tapping the target opens an in-tile
   editor (low / high with − / +, APPLY TARGET / CANCEL, 20 s timeout). Limits: SpO₂ low
   85–97, high 88–100, gap ≥ 2; etCO₂ low 25–50, high 30–60, gap ≥ 4. Changes are logged.
2. **Summary row**: Ppeak · Pplat · I:E · MVe · VTe · Leak. All read-only except I:E, which
   is a button: tapping it turns the tile into a stepper (− / +, APPLY / CANCEL, 12 s
   timeout) over 1:1 · 1:1.5 · 1:2 · 1:2.5 · 1:3 · 1:4. I:E is always user-controlled.
3. **Controls**: FiO₂ · PEEP · VT · RR, i.e. oxygenation pair under SpO₂, ventilation pair
   under etCO₂. Each: ownership band, name (identity colour), unit, value, − / +, reserved
   aux row (APPLY/CANCEL, or SUGGESTS…, or RETURN TO CLOVER), set time.
4. **Alerts band**: severity counts and LOG button on the left; alert rows with severity
   stripe, timestamp and ACK on the right. Shows NO ACTIVE ALERTS when quiet.

## 7. The loop icon

Three arc arrows (72° each) chasing around a circle. In AUTO it steps 12° once per
second (30 s per revolution, matching the decision cycle). In MANUAL and STOPPED the
rotation is **paused, not removed**, so the angle is preserved across mode changes, and a
diagonal slash (with a halo in the tile's background colour) cuts through it. The rotation
is applied to the arrows only; the slash never rotates.

## 8. Controller interaction rules (as implemented in the prototype)

- Decision cycle: 30 s. Oxygenation first (FiO₂, then PEEP), then ventilation (RR, then VT).
- **Out-of-range interrupt**: a value leaving target, escalating warn→crit, or losing
  signal triggers an immediate decision and restarts the cycle. Edge-triggered only.
- **UPDATE** button forces a decision immediately.
- **MANUAL**: controller computes and logs advisories, posts SUGGESTS chips, applies nothing.
- **STOPPED**: controller does nothing; ventilator holds current settings.
- **User override**: locks that parameter against the controller for 5 min, shown as a
  countdown; RETURN TO CLOVER cancels it early and forces an immediate decision.
- **Emergency O₂**: FiO₂ → 100% from the SpO₂ tile, arm + CONFIRM, works in any mode,
  counts as an override.
- **Ventilator link lost**: critical alert, controller holds and logs, all adjustment
  controls disabled, emergency O₂ refused and logged.

## 9. Not yet built

- Provider / RT screen (denser: waveforms or trends visible by default, more numbers,
  reasoning inline).
- Custom layout mode (choose which tiles show, within the same fixed grid).
- Interface contract for the real controller's data and decision stream, replacing the
  simulator.
- Replace the Zoll 731 EMV+ placeholder with the actual target ventilator.
- A signal-quality index beside each waveform, so "janky" is not left to judgment.
