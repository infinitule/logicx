# The wire, and what Logic does with it

Two halves: the Mackie Control Universal message set as implemented in
[`kit/mcu/protocol.py`](../kit/mcu/protocol.py), and the **Logic-specific
semantics** discovered by driving it — the second half is not in any protocol
document.

Device id `0x14`, SysEx header `F0 00 00 66 14`, MIDI channel 1 unless noted.

---

## Device → host (what we send)

| Control | Message |
|---|---|
| Button press | Note On `90 nn 7F`, release `90 nn 00`. 117 named notes; always released. |
| Fader | Pitch bend on channel `1+strip`: `Ex ll mm`, 14-bit **LSB first**, 0–16383. Master is `E8`. |
| Fader touch | Note `0x68+strip` (master `0x70`), velocity `7F` / `00`. A realistic move is touch-on → bend → touch-off. |
| V-Pot turn | CC `0x10+strip`, value `0x01–0x0F` clockwise, `0x41–0x4F` counter-clockwise. |
| V-Pot push | Note `0x20+strip`. |
| Jog wheel | CC `0x3C`, `0x01` / `0x41`. |

## Host → device (what Logic tells us)

| Feedback | Message | Notes |
|---|---|---|
| LED | Note On, velocity `00` off / `01` blink / `7F` on | Strip LEDs: rec `0x00–07`, solo `0x08–0F`, mute `0x10–17`, select `0x18–1F`. |
| Fader echo | Pitch bend | Sent on bank change and whenever the on-screen fader moves. |
| V-Pot ring | CC `0x30+strip` | `0x40` = centre LED; bits `0x30` = mode (dot / boost-cut / wrap / spread); `0x0F` = position 0–11. |
| Timecode | CC `0x40–0x49` | **`0x49` is the leftmost digit.** Char = `chr(0x40+v)` if `(v & 0x3F) < 0x20` else `chr(v & 0x3F)`; bit `0x40` = decimal point. |
| Assignment display | CC `0x4B` (left), `0x4A` (right) | Two characters. A trailing period means *Channel view* — see below. |
| LCD | SysEx `12 <offset 0–111> <ascii…>` | 112 cells: 0–55 top row, 56–111 bottom. 7 cells per strip. Partial writes are normal. |
| Meters | Channel pressure `D0 vv` | strip = `vv >> 4`, level = `vv & 0x0F`; `0x0E` sets the clip indicator, `0x0F` clears it. |

## Handshake

```
host  → F0 00 00 66 14 00 F7                      device query
device→ F0 00 00 66 14 01 <serial 7> <chal 4> F7  host connection query
host  → F0 00 00 66 14 02 <serial 7> <resp 4> F7  host connection reply
device→ F0 00 00 66 14 03 <serial 7> F7           confirmation
```

Logic sends the device query **unprompted** to every MIDI port that appears
while it is running, and again at launch. That is why no configuration is
required: open the port, answer, and Logic adopts the surface.

## Logic-specific semantics (the undocumented half)

### The assignment display is the mode, and the period matters

The two-character display is the only indication of which of Logic's views the
V-Pots are wired to, and a trailing period changes the meaning of every knob:

| Display | View | What V-Pot *n* does |
|---|---|---|
| `tr` | Track **Mixer** view | one parameter across **eight different strips** |
| `tr.` | Track **Channel** view | eight parameters of **one strip** |
| `PN` | Pan Mixer view | strip *n*'s pan |
| `PN.` | Pan **Channel** view | the selected strip's object panner: 4 = Spread, 5 = L/R, 6 = B/F, 7 = Object Size, 8 = Elevation |
| `P1`…`P9`, `10`…`16` | Plug-in Mixer view | the plug-in in that insert slot per strip |
| `P1.`…`P8.` | Plug-in **Edit** view | parameters of that plug-in, in pages of eight |
| `CS` | Channel Strip parameter (Track Mixer) | cursor left/right cycles Volume → Surr. Angle → Channel Format → Channel Input → Channel Output → Automation Mode |

[`objpan.py`](../kit/mcu/objpan.py) refuses to move anything unless the display
reads `PN.`, because in `PN` the same turn edits a different track.

### Values are text, and the text is truncated

The LCD cell is seven characters. Logic writes `-6.0 dB` in full but truncates to
`-57.4 d` when the number is longer — the trailing `B` is simply gone. A parser
that requires `dB` silently rejects every value below about −10 dB.

The value row also shows **parameter names** rather than values until NAME/VALUE
is pressed, so a reader must confirm it is in value mode before parsing.

### Menus preselect; the push commits

Enumerated parameters (outputs, plug-ins, sends) work as *preselect then
confirm*: a V-Pot turn flashes a candidate, and only a V-Pot push applies it.
CANCEL abandons it. A turn that is never pushed leaves the parameter unchanged
while the display shows the candidate — which reads exactly like success.

The output list is `Surr. L/R → Surround → No Output → Stereo → Bus 1 … Bus 256`
and wraps. There is **no `Object` entry**: object routing is unreachable from a
control surface in Logic 12.3.1.

### Alerts arrive on the LCD

When Logic raises a modal dialog, its text is written to the LCD and the button
labels appear in the value row:

```
top  |The project will be converted to 7.1.2 surround for mix |
val  |                                   OK     Cancel Do'tAs |
```

A V-Pot push under the label answers it. This is the single most useful
undocumented behaviour found: the state that blinds every other surface is
readable and answerable on this one.

### The REC LED follows selection

`rec` lights for the *selected* audio track even when its record button is off.
Do not infer arm state from that LED; read it from accessibility instead.

### Second STOP returns to the start

From Apple's own transport table, confirmed live: pressing STOP again after
stopping returns the playhead to the project start (or the cycle start). There is
no "go to beginning" note in the protocol.

## Fader units ↔ dB

Faders are 14-bit units with no documented mapping to dB, and the curve is
non-linear at the bottom. `mcuctl calibrate <strip>` sweeps the fader, reads
Logic's own dB text at each stop, and writes the pairs to `calibration.json`
(`{"strip": n, "pairs": [[fader14, dB or null], …]}`). Measured on Logic 12.3.1:

| fader | dB |
|---|---|
| 0 | −∞ |
| 6479 | −15.1 |
| 8739 | −8.1 |
| 9874 | −6.1 |
| **12443** | **0.0** (unity) |
| 14958 | +6.0 (top) |

`fader <strip> <dB>` interpolates from that table and then reports the echo, so
the number that comes back is Logic's, not ours.

## What the implementation refuses to send

[`validate_midi()`](../kit/mcu/daemon.py#L94) rejects, before anything reaches
CoreMIDI:

- any data byte ≥ `0x80` (it becomes a *new message* on the wire — `90 DF 7F`
  was delivered to Logic as `90 5F 7F`, RECORD)
- a SysEx without its terminating `F7` (it swallows every later message until one
  arrives)
- a status byte outside `0x80–0xEF`, or a channel message of the wrong length

Every one of those was a live defect found by an adversarial review of this
code, reproduced against a fake host, and is now a test in
[`test_review_fixes.py`](../kit/mcu/tests/test_review_fixes.py).
