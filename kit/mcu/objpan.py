#!/usr/bin/env python3
"""Closed-loop 3D Object Panner control over the Mackie Control surface.

Logic's Pan/Surround CHANNEL view (assignment display "Pn.") maps the selected
strip's object panner onto V-Pots: 4=Spread(deg) 5=L/R(-100..100)
6=B/F(-100..100, 100=front) 7=Object Size 8=Elevation(0..100). Values are read
from the LCD after every turn — a placement is only reported when Logic's own
display confirms it.

    python3 objpan.py place <bank-strip 1-8> spread=180 lr=0 bf=100 elev=15
"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

MCUCTL = str(Path(__file__).resolve().parent.parent / "mcuctl")
VPOT = {"spread": 4, "lr": 5, "bf": 6, "size": 7, "elev": 8}
STEP = {"spread": 3.1, "lr": 3.1, "bf": 3.1, "size": 3.1, "elev": 1.6}   # per tick, measured
TOL = {"spread": 2.0, "lr": 1.6, "bf": 1.6, "size": 1.6, "elev": 1.0}
NUM = re.compile(r"^\s*([+-]?\d+(?:\.\d+)?)")


def run(*args):
    return subprocess.run([MCUCTL, "--timeout", "6", *args], capture_output=True, text=True, timeout=30).stdout


def cell(strip):
    out = run("lcd", "--json")
    try:
        return json.loads(out)["strips"][strip - 1]["value"]
    except (ValueError, KeyError, IndexError):
        return ""


def value(strip):
    m = NUM.match(cell(strip))
    return float(m.group(1)) if m else None


def wake(strip):
    """First tick after a page change often only 'wakes' the readout."""
    run("vpot", str(strip), "1"); time.sleep(0.35)
    run("vpot", str(strip), "-1"); time.sleep(0.35)


def set_param(strip, name, target, max_rounds=30):
    pot = VPOT[name]
    wake(pot)
    start = value(pot)
    if start is None:
        return {"param": name, "ok": False, "err": f"no numeric readout in cell {pot} ({cell(pot)!r})"}
    cur = start
    for _ in range(max_rounds):
        diff = target - cur
        if abs(diff) <= TOL[name]:
            break
        ticks = max(-15, min(15, round(diff / STEP[name]) or (1 if diff > 0 else -1)))
        run("vpot", str(pot), str(ticks))
        time.sleep(0.4)
        new = value(pot)
        if new is None:
            break
        if new == cur and abs(target - new) > TOL[name] and abs(ticks) == 1:
            break                      # clamped at a bound (e.g. Spread max)
        cur = new
    return {"param": name, "ok": abs(target - cur) <= TOL[name] or cur == start and False or abs(target - cur) <= TOL[name],
            "start": start, "end": cur, "target": target}


def assign_text():
    out = run("time")
    m = re.search(r"assign:\s*'([^']*)'", out)
    return m.group(1) if m else ""


def ensure_channel_view():
    """Assignment display must read 'PN.' (Pan CHANNEL view). 'PN' without the dot is
    the MIXER view, where every V-Pot is a DIFFERENT STRIP's pan — turning one there
    edits the wrong track (verified the hard way)."""
    for _ in range(3):
        a = assign_text()
        if a == "PN.":
            return True
        run("press", "pan")
        time.sleep(0.7)
    return assign_text() == "PN."


def ensure_value_mode():
    """In Name mode the value row shows parameter names; NAME/VALUE toggles it."""
    if NUM.match(cell(VPOT["bf"]) or ""):
        return
    run("press", "name_value")
    time.sleep(0.6)


def place(strip, targets):
    run("select", str(strip), "on")
    time.sleep(0.5)
    if not ensure_channel_view():
        return [{"param": k, "ok": False, "err": f"not in Pan Channel view (assign {assign_text()!r})"} for k in targets]
    ensure_value_mode()
    results = [set_param(strip, k, v) for k, v in targets.items()]
    label = run("lcd", "--json")
    try:
        name = json.loads(label)["top"] if False else ""
    except ValueError:
        name = ""
    return results


def main():
    if len(sys.argv) < 3 or sys.argv[1] != "place":
        print(__doc__)
        return 1
    strip = int(sys.argv[2])
    targets = {}
    for arg in sys.argv[3:]:
        k, _, v = arg.partition("=")
        if k not in VPOT:
            print(f"unknown parameter {k!r}; use {', '.join(VPOT)}")
            return 1
        targets[k] = float(v)
    for r in place(strip, targets):
        status = "ok " if r.get("ok") else "OFF"
        if "err" in r:
            print(f"  {r['param']:6} !! {r['err']}")
        else:
            print(f"  {r['param']:6} {status} {r['start']:>7.1f} -> {r['end']:>7.1f}  (target {r['target']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
