#!/usr/bin/env python3
"""Generate every diagram in this repo from the numbers it cites.

    python3 assets/make_diagrams.py

Every bar length here is a real ratio of a measured quantity. Nothing is drawn
by hand and nothing is scaled "for looks" — if a number changes, re-run this and
the picture changes with it.

Requires the repo-taste design kit:
    ~/.claude/skills/repo-taste/kit/svgkit.py
"""
import os
import pathlib
import sys

sys.path.insert(0, os.path.expanduser("~/.claude/skills/repo-taste/kit"))
from svgkit import *  # noqa: E402,F401,F403

OUT = pathlib.Path(__file__).resolve().parent

# ── measured facts (see README "The measurement that justifies it") ──────────
TOK_SHOT = 2352      # one 1680x1050 screenshot, w*h/750
TOK_STRIPS = 510     # `logic.py mixer strips` — 16 strips x 10 fields, 2042 B
TOK_STATUS = 118     # `mcuctl status` — transport, LEDs, display, names, 472 B

# the nine objects actually placed in the Atmos rebuild: (label, L/R, B/F, elev, spread)
OBJECTS = [
    ("Vocals",        0, 100, 15, 180),
    ("Vocals (Sec)",  0, 100, 15, 180),
    ("Back Vox",      0, -40, 25, None),
    ("Back Vox (Sec)", 0, -40, 25, None),
    ("FX",            0, -60, 35, 120),
    ("FX (Sec)",      0, -60, 35, 120),
    ("Synth",         0,  30, 50, 100),
    ("Synth (Sec)",   0,  30, 50, 100),
    ("Guitar (Sec)", -50, 50, 20, None),
]


def write(name, w, h, body):
    (OUT / name).write_text(svg(w, h, body))
    print(f"  {name}  {w}x{h}")


# ── 1. banner: the whole thesis in one image ────────────────────────────────
def banner():
    W, H = 1200, 430
    b = defs(marker("ar", DIM), marker("arR", RED), marker("arG", GRN))
    b += txt(44, 52, "Claude does not look at Logic Pro. Claude is a Mackie Control.", 19, TXT, w="600")
    b += txt(44, 78, "Protocol-native app control: the agent speaks a machine interface the app already trusts.", 12.5, MUT)

    # left lane — the screenshot loop
    b += rect(44, 108, 520, 286, PANEL2, EDGE)
    b += txt(68, 140, "THE SCREENSHOT LOOP", 11.5, RED, w="600")
    b += txt(68, 162, "drive a UI built for human eyes", 11.5, DIM)
    steps = ["screenshot  ~2,352 tokens", "find the fader by pixel", "click / drag  (hope)",
             "screenshot again to check", "still no exact dB value"]
    for i, s in enumerate(steps):
        y = 190 + i * 38
        b += rect(68, y, 472, 28, PANEL, EDGE, 8)
        b += txt(84, y + 19, s, 11.5, MUT if i < 4 else RED, mono=True)
        if i < 4:
            b += arrow(304, y + 28, 304, y + 38, RED, "arR")

    # right lane — the protocol loop
    b += rect(600, 108, 556, 286, PANEL2, EDGE)
    b += txt(624, 140, "THE PROTOCOL LOOP", 11.5, GRN, w="600")
    b += txt(624, 162, "speak what the app already listens for", 11.5, DIM)

    b += rect(624, 190, 196, 84, PANEL, GRN, 10)
    b += txt(722, 218, "Claude", 14, TXT, "middle", "600")
    b += txt(722, 240, "mcu/daemon.py", 10.5, MUT, "middle", mono=True)
    b += txt(722, 258, "virtual CoreMIDI port", 10.5, DIM, "middle")

    b += rect(960, 190, 172, 84, PANEL, BLUE, 10)
    b += txt(1046, 218, "Logic Pro", 14, TXT, "middle", "600")
    b += txt(1046, 240, "12.3.1", 10.5, MUT, "middle", mono=True)
    b += txt(1046, 258, "adopts it itself", 10.5, DIM, "middle")

    b += txt(890, 214, "faders · V-Pots · buttons", 9.5, DIM, "middle")
    b += arrow(826, 224, 954, 224, GRN, "arG")
    b += txt(890, 250, "names · dB · LEDs · meters", 9.5, DIM, "middle")
    b += arrow(954, 260, 826, 260, BLUE, "ar")

    b += rect(624, 296, 508, 76, PANEL, EDGE, 10)
    b += txt(648, 322, "every change is reported from Logic's echo,", 12, TXT, w="600")
    b += txt(648, 344, "never from the command that was sent.", 12, GRN, w="600")
    b += txt(648, 362, "a fader move that did not land looks exactly like one that did", 10.5, DIM)
    return write("banner.svg", W, H, b)


# ── 2. cost: proportional context cost of one "read the mixer" ──────────────
def cost():
    W, H = 1100, 470
    b = txt(44, 46, "What it costs to know the state of the mix", 17, TXT, w="600")
    b += txt(44, 70, "Context tokens for ONE read — screenshot by the documented w×h/750 formula, text by bytes/4.",
             11.5, MUT)
    rows = [
        (TOK_SHOT, RED, "2,352 tok", "one screenshot (1680×1050)",
         "shows only what is visible · no exact values · must be re-shot after every action"),
        (TOK_STRIPS, GRN, "510 tok", "logic.py mixer strips — 16 strips × 10 fields",
         "dB, pan, mute, solo, rec, monitor, automation mode, output, format, peak"),
        (TOK_STATUS, BLUE, "118 tok", "mcuctl status — the whole surface",
         "transport + automation LEDs, assignment display, playhead, 8 track names"),
    ]
    y = 116
    BARW = 640
    for val, col, lab, title, sub in rows:
        b += txt(44, y - 10, title, 12.5, TXT, w="600")
        b += hbar(44, y, BARW, 30, val / TOK_SHOT, col)
        b += txt(44 + BARW + 18, y + 21, lab, 11.5, col, mono=True)
        b += txt(44, y + 50, sub, 10.5, DIM)
        y += 92
    b += rect(44, y - 6, 1012, 74, PANEL2, EDGE)
    b += txt(68, y + 22, "The screenshot costs 4.6× the complete table — and carries less.", 12, TXT, w="600")
    b += txt(68, y + 46, "It cannot show a strip scrolled off-screen, a dB value to one decimal, or a mute you "
                         "did not think to look at.", 11.5, MUT)
    return write("cost.svg", W, H, b)


# ── 3. surfaces: the ladder, narrowest first ────────────────────────────────
def surfaces():
    W, H = 1200, 470
    b = txt(44, 46, "Five surfaces, narrowest first", 17, TXT, w="600")
    b += txt(44, 70, "Each rung is used only for what the rung above cannot reach. Screenshots are the last resort, "
                     "not the default.", 11.5, MUT)
    rungs = [
        ("1", "Mackie Control", GRN,
         ["faders · V-Pots · buttons", "LCD names + values · LEDs",
          "meters · playhead", "no focus, no window,", "works in the background"]),
        ("2", "Accessibility", BLUE,
         ["dB text · pan · mute/solo", "rec · monitor · automation", "output · format · peak",
          "needs Logic frontmost —", "costs a focus flicker"]),
        ("3", "Menu tree", TEAL,
         ["every command the app has,", "discovered from the", "running app itself",
          "invoked by name,", "never by coordinate"]),
        ("4", "OSC (Renderer)", PUR,
         ["transport · monitoring", "dim · mute · layout",
          "13 documented addresses", "fire-and-forget:", "confirm elsewhere"]),
        ("5", "computer-use", RED,
         ["third-party plug-in windows", "anything the four above", "cannot reach",
          "screenshot to read —", "and say that you did"]),
    ]
    x = 44
    cw = 216
    for num, title, col, lines in rungs:
        b += card(x, 108, cw, 200, title, lines, col)
        b += txt(x + cw / 2, 336, num, 26, col, "middle", "700", op=0.35)
        x += cw + 20
    b += rect(44, 360, 1112, 78, PANEL2, EDGE)
    b += txt(68, 388, "The rule that makes the ladder work", 12.5, TXT, w="600")
    b += txt(68, 412, "A surface is only allowed if it can be read back. Every setter re-reads the control it moved and "
                      "reports before → after;", 11.5, MUT)
    b += txt(68, 430, "a change that cannot be verified is reported as a failure, never as success.", 11.5, MUT)
    return write("surfaces.svg", W, H, b)


# ── 4. loop: closed-loop control, the actual algorithm ──────────────────────
def loop():
    W, H = 1160, 400
    b = defs(marker("ar", DIM))
    b += txt(44, 46, "Closed-loop control: the app's own display is the sensor", 17, TXT, w="600")
    b += txt(44, 70, "Logic's fader is not settable. `set value` nudges ±1 unit (≈0.1 dB), AXIncrement steps ±10 "
                     "(≈1 dB), and the dB lives in the element's NAME.", 11.5, MUT)
    boxes = [
        ("read", ["parse the dB text", "that Logic renders"], BLUE),
        ("decide", ["|diff| ≥ 0.95 dB → step", "smaller → nudge"], TEAL),
        ("act", ["one AX action, or", "one MIDI message"], GRN),
        ("re-read", ["did the value move?", "stale twice → abort"], AMB),
    ]
    x = 44
    for i, (t, lines, col) in enumerate(boxes):
        b += card(x, 108, 240, 110, t, lines, col)
        if i < 3:
            b += arrow(x + 240, 163, x + 268, 163, DIM)
        x += 268
    b += arrow(1092, 218, 1092, 248, DIM)
    b += arrow(1092, 248, 168, 248, DIM)
    b += txt(600, 240, "loop until |target − current| ≤ 0.05 dB, or 120 steps, or the readout stops moving",
             11, DIM, "middle")

    b += rect(44, 276, 1072, 96, PANEL2, GRN)
    b += txt(68, 304, "Real output, copied from a run", 12, TXT, w="600")
    b += txt(68, 330, "Audio 1 volume: -6.0 dB -> 0.0 dB  (target 0.0, 6 steps, raw 173, 5.25s)", 11.5, GRN, mono=True)
    b += txt(68, 354, "fader 2: sent 9913 (-6.0 dB); host fader 12443 -> 9874 (-6.1 dB), LCD '-6.0 dB'", 11.5, GRN, mono=True)
    return write("loop.svg", W, H, b)


# ── 5. atmos field: the placement actually written into the project ─────────
def atmos():
    W, H = 1160, 620
    b = txt(44, 46, "The Atmos rebuild, as configured — not as intended", 17, TXT, w="600")
    b += txt(44, 70, "Nine object placements written over the control surface and confirmed by Logic's own readout. "
                     "Plan view: listener at centre, front is up.", 11.5, MUT)

    cx, cy, rad = 300, 330, 200
    b += rect(70, 100, 460, 460, PANEL2, EDGE)
    b += f'<circle cx="{cx}" cy="{cy}" r="{rad}" fill="none" stroke="{EDGE}" stroke-width="1.25"/>'
    b += f'<circle cx="{cx}" cy="{cy}" r="{rad*0.5}" fill="none" stroke="{EDGE}" stroke-width="1" stroke-dasharray="3 5"/>'
    b += f'<line x1="{cx-rad}" y1="{cy}" x2="{cx+rad}" y2="{cy}" stroke="{EDGE}" stroke-width="1"/>'
    b += f'<line x1="{cx}" y1="{cy-rad}" x2="{cx}" y2="{cy+rad}" stroke="{EDGE}" stroke-width="1"/>'
    b += txt(cx, 128, "FRONT", 10.5, DIM, "middle")
    b += txt(cx, 552, "REAR", 10.5, DIM, "middle")
    b += f'<circle cx="{cx}" cy="{cy}" r="5" fill="{MUT}"/>'
    b += txt(cx + 12, cy + 4, "listener", 10, DIM)

    seen = {}
    for label, lr, bf, elev, spread in OBJECTS:
        px = cx + (lr / 100) * rad
        py = cy - (bf / 100) * rad
        key = (round(px), round(py))
        seen[key] = seen.get(key, 0) + 1
        px += (seen[key] - 1) * 13          # separate the main/Section pairs
        r = 6 + elev / 10                   # radius carries elevation
        b += f'<circle cx="{px:.0f}" cy="{py:.0f}" r="{r:.1f}" fill="{PUR}" opacity="0.22"/>'
        b += f'<circle cx="{px:.0f}" cy="{py:.0f}" r="{r:.1f}" fill="none" stroke="{PUR}" stroke-width="1.3"/>'
    b += txt(90, 540, "bubble size = elevation (0–100)", 10.5, DIM)

    b += txt(566, 128, "OBJECT", 10, DIM, w="600")
    b += txt(768, 128, "L/R", 10, DIM, "end", "600")
    b += txt(830, 128, "B/F", 10, DIM, "end", "600")
    b += txt(892, 128, "ELEV", 10, DIM, "end", "600")
    b += txt(966, 128, "SPREAD", 10, DIM, "end", "600")
    y = 152
    for label, lr, bf, elev, spread in OBJECTS:
        b += txt(566, y, label, 11.5, TXT)
        b += txt(768, y, f"{lr}", 11.5, MUT, "end", mono=True)
        b += txt(830, y, f"{bf}", 11.5, MUT, "end", mono=True)
        b += txt(892, y, f"{elev}", 11.5, MUT, "end", mono=True)
        b += txt(966, y, "—" if spread is None else f"{spread}", 11.5, MUT, "end", mono=True)
        y += 24
    y += 12
    b += rect(566, y, 550, 122, PANEL, EDGE)
    b += txt(590, y + 28, "Bed and gain staging", 12.5, TXT, w="600")
    for i, line in enumerate([
        "bed     the stereo master on Audio 14, Surround out, -2.5 dB",
        "muted   bass + drums stems, main and Section (they double the bed)",
        "objects 9 garnish stems at -15 dB under the bed",
        "fixed   empty Audio 16 Object -> Surround: 29 ch -> 27 ch",
    ]):
        b += txt(590, y + 54 + i * 18, line, 10.5, MUT, mono=True)
    return write("atmos-field.svg", W, H, b)


if __name__ == "__main__":
    print("writing diagrams:")
    banner(); cost(); surfaces(); loop(); atmos()
    print("now LOOK at them:  python3 ~/.claude/skills/repo-taste/kit/verify.py assets")
