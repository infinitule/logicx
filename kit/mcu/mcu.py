#!/usr/bin/env python3
"""Drive Logic Pro as a Mackie Control surface from the shell.

A daemon owns a pair of virtual CoreMIDI ports called "Claude MCU" and speaks
Mackie Control. Logic detects the surface on the handshake (or add it under
Control Surfaces > Setup > New > Install > Mackie Control with those ports) and
then streams track names, values, fader positions, LEDs, meters and the
playhead to us; we send button presses, fader moves and V-Pot turns back.
No screenshots, no UI scripting.

    M=~/.claude/skills/logic-atmos-engineer/kit/mcuctl
    $M daemon start                 # open the ports, then let Logic find them
    $M status                       # daemon, handshake, transport, time, strip names
    $M lcd                          # both LCD lines with the 8 strip columns
    $M strips                       # names, values, faders, LEDs, rings, meters
    $M transport play               # press play, report the transport LEDs
    $M fader 3 -6                   # strip 3 to -6 dB (run `calibrate 3` once)
    $M fader 3 pct:75               #   or 75% of travel, or raw:12288 (0-16383)
    $M mute 3 on                    # idempotent: presses only if the LED says off
    $M select 2 on && $M auto 2 touch
    $M mode pan && $M vpot 2 -5     # assignment button, then V-Pot turns
    $M bank right                   # next 8 tracks; `channel left` shifts by one
    $M watch --seconds 10           # stream every decoded host message
    $M daemon stop

Strips are 1-8 (or master) as on the hardware. Buttons are named as in
protocol.BUTTONS (play, bank_left, mute3, f1, 0x2E ...), case-insensitive,
with -, _ and space interchangeable. Record-arm, record, replace and drop need
--yes. --json on any command gives machine-readable output.

Exit codes: 0 ok, 1 error, 2 daemon not running (the message says how to start
it). The run dir (socket, pidfile, log) is kit/mcu/run; override with
--run-dir or $MCU_RUN_DIR. Actions that expect an echo from Logic wait
--timeout ms (default 1500) for it and say so when nothing comes back.
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

if __package__ in (None, ""):
    # absolute(), not resolve(): keep the ~/.claude/... spelling the user typed, so
    # paths in messages stay short even when the skill dir is a symlink.
    sys.path.insert(0, str(Path(__file__).absolute().parent.parent))
    __package__ = "mcu"

from . import daemon as daemon_mod  # noqa: E402
from . import protocol as p  # noqa: E402
from .client import Client, DaemonError, DaemonNotRunning  # noqa: E402

HERE = Path(__file__).absolute().parent
LAUNCHER = HERE.parent / "mcuctl"
CALIBRATION = Path(os.environ.get("MCU_CALIBRATION") or HERE / "calibration.json")
TRANSPORT = {"play": "play", "stop": "stop", "rewind": "rewind", "rew": "rewind",
             "ff": "fast_fwd", "fast_fwd": "fast_fwd", "record": "record", "rec": "record"}
AUTO_MODES = {"read": "read_off", "write": "write", "trim": "trim", "touch": "touch", "latch": "latch", "off": None}
# Logic prints "-57.4 d" when "dB" does not fit the 7-character LCD cell.
DB_RE = re.compile(r"^\s*([+-]?\d+(?:\.\d+)?)\s*(?:dB|d)?\s*$", re.I)
NEG_INF_RE = re.compile(r"^\s*-?\s*(inf|infinity|oo|∞)\s*(?:dB)?\s*$", re.I)


# --- helpers ------------------------------------------------------------------

def warn(message):
    print(message, file=sys.stderr)


def strip_arg(text, allow_master=True):
    """'1'..'8' -> 0..7, 'master' -> 8."""
    key = str(text).strip().lower()
    if key in ("master", "m") and allow_master:
        return p.MASTER
    if key.isdigit() and 1 <= int(key) <= 8:
        return int(key) - 1
    extra = " or master" if allow_master else ""
    sys.exit(f"strip must be 1-8{extra}, not {text!r}")


def strip_label(strip):
    return "master" if strip == p.MASTER else str(strip + 1)


def led_text(value):
    return p.led_state_name(value)


def age(ts):
    if not ts:
        return "never"
    seconds = time.time() - ts
    if seconds < 60:
        return f"{seconds:.1f}s ago"
    if seconds < 3600:
        return f"{seconds / 60:.0f}m ago"
    return f"{seconds / 3600:.1f}h ago"


def uptime(seconds):
    seconds = int(seconds or 0)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m{seconds % 60:02d}s"
    return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"


def emit(args, data, text):
    print(json.dumps(data, indent=2) if args.json else text)


def require_yes(args, what):
    if not args.yes:
        sys.exit(f"'{what}' is destructive (record-arm/record/replace/drop) — pass --yes to confirm.")


def parse_db_text(text):
    """LCD value cell -> dB float, None for -inf. Raises ValueError when it is not a level."""
    if NEG_INF_RE.match(text):
        return None
    match = DB_RE.match(text)
    if not match:
        raise ValueError(f"{text!r} does not look like a dB value")
    return float(match.group(1))


class Calibration:
    """fader14 <-> dB from calibration.json ([[fader14, dB|null], ...], sorted)."""

    def __init__(self, pairs):
        self.pairs = sorted((int(v), None if d is None else float(d)) for v, d in pairs)
        self.known = [(v, d) for v, d in self.pairs if d is not None]
        if not self.known:
            raise ValueError("calibration has no finite dB values")

    @classmethod
    def load(cls, path=CALIBRATION):
        if not path.exists():
            raise FileNotFoundError(
                f"no calibration at {path}. Run once with Logic connected:  {LAUNCHER} calibrate 1")
        data = json.loads(path.read_text())
        return cls(data["pairs"] if isinstance(data, dict) else data)

    def db_to_fader(self, db):
        """Linear interpolation between calibrated points, clamped at the ends; -inf -> 0."""
        if db is None:
            return 0
        (lo_v, lo_db), (hi_v, hi_db) = self.known[0], self.known[-1]
        if db <= lo_db:
            return lo_v
        if db >= hi_db:
            return hi_v
        for (v0, d0), (v1, d1) in zip(self.known, self.known[1:]):
            if d0 <= db <= d1:
                return round(v0 if d1 == d0 else v0 + (db - d0) / (d1 - d0) * (v1 - v0))
        return hi_v

    def fader_to_db(self, value14):
        """Inverse of db_to_fader. Below the lowest finite point counts as -inf (None)."""
        (lo_v, lo_db), (hi_v, hi_db) = self.known[0], self.known[-1]
        if value14 < lo_v:
            return None if self.pairs[0][1] is None else lo_db
        if value14 >= hi_v:
            return hi_db
        for (v0, d0), (v1, d1) in zip(self.known, self.known[1:]):
            if v0 <= value14 <= v1:
                return d0 if v1 == v0 else d0 + (value14 - v0) / (v1 - v0) * (d1 - d0)
        return hi_db


def load_calibration_or_none():
    try:
        return Calibration.load()
    except (FileNotFoundError, ValueError, KeyError):
        return None


def format_db(db):
    if db is None:
        return "-inf"
    if abs(db) < 0.05:
        return "0.0"
    return f"{db:+.1f}" if db > 0 else f"{db:.1f}"


def transport_action(text):
    key = text.strip().lower()
    if key not in TRANSPORT:
        raise argparse.ArgumentTypeError(f"choose from play, stop, rewind, ff, record (not {text!r})")
    return key


def parse_fader_value(text):
    """raw:NNNN | pct:NN | <dB> -> (value14, description)."""
    key = text.strip().lower()
    if key.startswith("raw:"):
        value = int(key[4:])
        if not 0 <= value <= 16383:
            sys.exit("raw value must be 0-16383")
        return value, f"raw {value}"
    if key.startswith("pct:") or key.endswith("%"):
        pct = float(key[4:] if key.startswith("pct:") else key[:-1])
        if not 0 <= pct <= 100:
            sys.exit("pct must be 0-100")
        return round(pct / 100 * 16383), f"{pct:g}% of travel"
    try:
        db = parse_db_text(key)
    except ValueError:
        sys.exit(f"fader value {text!r}: use raw:NNNN (0-16383), pct:NN (0-100) or a dB number like -6, 0, +3")
    try:
        cal = Calibration.load()
    except (FileNotFoundError, ValueError, KeyError) as exc:
        sys.exit(f"{exc}\n(dB values need calibration.json; raw:NNNN and pct:NN work without it)")
    return cal.db_to_fader(db), f"{format_db(db)} dB"


def render_lcd(state):
    ruler = "      " + "".join(f"{i + 1:<7}" for i in range(8))
    return "\n".join([ruler, f"top  |{state['lcd_top']}|", f"val  |{state['lcd_bottom']}|"])


def lcd_json(state):
    return {"top": state["lcd_top"], "bottom": state["lcd_bottom"],
            "strips": [{"idx": s["idx"], "name": s["name"], "value": s["value"]} for s in state["strips"]]}


def transport_text(state):
    leds = state["leds"]
    parts = [f"{n}={led_text(leds.get(n, 0))}" for n in ("play", "stop", "record", "rewind", "fast_fwd", "cycle")]
    return "  ".join(parts)


def automation_text(state):
    leds = state["leds"]
    return "  ".join(f"{n}={led_text(leds.get(n, 0))}" for n in p.AUTOMATION_BUTTONS)


def time_mode(state):
    leds = state["leds"]
    if leds.get("smpte_led"):
        return "smpte"
    if leds.get("beats_led"):
        return "beats"
    return "?"


def meter_bar(strip):
    level = strip["meter_level"] or 0
    bar = "#" * level + "." * (12 - level)
    return bar + (" CLIP" if strip["meter_clip"] else "")


def render_strips(state, cal):
    head = f"{'#':>2} {'name':<7}{'value':<7}{'fader':>6} {'dB':>6}  {'rec':<5}{'solo':<5}{'mute':<5}{'sel':<5} {'vpot':<16} meter"
    rows = [head]
    for s in state["strips"]:
        ring = s["vpot"]
        vpot = f"{ring['mode_name']} {ring['pos']:>2}{' *' if ring['center'] else ''}"
        db = format_db(cal.fader_to_db(s["fader14"])) if cal else "?"
        rows.append(
            f"{s['idx']:>2} {s['name'][:6]:<7}{s['value'][:6]:<7}{s['fader14']:>6} {db:>6}  "
            f"{led_text(s['rec']):<5}{led_text(s['solo']):<5}{led_text(s['mute']):<5}{led_text(s['select']):<5} "
            f"{vpot:<16} {meter_bar(s)}")
    mdb = format_db(cal.fader_to_db(state["master_fader14"])) if cal else "?"
    rows.append(f" M {'master':<7}{'':<7}{state['master_fader14']:>6} {mdb:>6}")
    return "\n".join(rows)


def wait_echo(args, c, what, seq, **filters):
    """Wait for a host echo; warn (do not fail) when it never comes."""
    reply = c.wait(what, timeout_ms=args.timeout, since=seq, **filters)
    if reply["timed_out"]:
        warn(f"no {what} echo from the host within {args.timeout} ms "
             f"(is Logic connected? check `{LAUNCHER.name} status`)")
    return reply


def press_and_wait(args, c, button, what, **filters):
    seq = c.state()["seq"]
    c.press(button, getattr(args, "hold", None))
    return wait_echo(args, c, what, seq, **filters)


# --- commands -----------------------------------------------------------------

def cmd_daemon(args, run_dir):
    if args.action == "start":
        try:
            device_id = daemon_mod.parse_device_id(args.device_id)
            info = daemon_mod.start_background(run_dir, args.port_name, device_id, verbose=args.verbose)
        except (DaemonError, ValueError) as exc:
            sys.exit(f"could not start the daemon: {exc}")
        emit(args, info, f"daemon started: pid {info['pid']}, MIDI ports {info.get('port_name')!r} "
                         f"(device id {info.get('device_id')}), socket {info['socket']}\n"
                         "Logic should now detect a Mackie Control on those ports; `status` shows the handshake.")
        return 0
    if args.action == "stop":
        was = daemon_mod.stop(run_dir)
        emit(args, {"stopped": was}, "daemon stopped" if was else "daemon was not running")
        return 0
    if args.action == "log":
        print(daemon_mod.log_tail(run_dir, args.lines) or f"(no log yet at {run_dir / 'daemon.log'})")
        return 0
    info = daemon_mod.status(run_dir)
    if args.json:
        print(json.dumps(info, indent=2))
    elif info["running"]:
        print(f"daemon running: pid {info['pid']}, up {uptime(info.get('uptime_s'))}, "
              f"ports {info.get('port_name')!r} (device id {info.get('device_id')}), "
              f"handshake {info.get('handshake')}, online {'yes' if info.get('online') else 'no'}, "
              f"last host message {age(info.get('last_host_ts'))}\n"
              f"socket {info['socket']}  log {run_dir / 'daemon.log'}")
    else:
        print(f"daemon not running (run dir {run_dir}). Start it with:  {LAUNCHER} daemon start")
    return 0 if info["running"] else 2


def cmd_status(args, c, run_dir):
    reply = c.state()
    state, info = reply["state"], reply["daemon"]
    if args.json:
        print(json.dumps({"daemon": {**info, "running": True}, "state": state, "seq": reply["seq"]}, indent=2))
        return 0
    names = "  ".join(f"{s['idx']} {s['name'] or '-'}" for s in state["strips"])
    lines = [
        f"daemon:    running, pid {info['pid']}, up {uptime(info['uptime_s'])}, "
        f"ports {info['port_name']!r} (device id {info['device_id']}), {reply['seq']} events",
        f"handshake: {state['handshake']} ({age(state['handshake_ts'].get(state['handshake']))})"
        f"   online: {'yes' if state['online'] else 'no'}"
        f"   last host message: {age(state['last_host_ts'])} ({state['counters'].get('total', 0)} total)",
        f"transport: {transport_text(state)}",
        f"auto:      {automation_text(state)}",
        f"display:   assign {state['assign_text']!r}   time {state['time_text']!r} ({time_mode(state)})",
        f"strips:    {names}",
    ]
    if state["handshake"] == "none":
        lines.append("Logic has not talked to us yet. In Logic: Control Surfaces > Setup > New > Install > "
                     f"Mackie Control, with input and output port {info['port_name']!r}.")
    print("\n".join(lines))
    return 0


def cmd_lcd(args, c):
    state = c.state()["state"]
    emit(args, lcd_json(state), render_lcd(state))
    return 0


def cmd_strips(args, c):
    cal = load_calibration_or_none()
    while True:
        state = c.state()["state"]
        if args.json:
            for s in state["strips"]:
                s["fader_db"] = cal.fader_to_db(s["fader14"]) if cal else None
            print(json.dumps(state["strips"], indent=2))
        else:
            print(render_strips(state, cal))
        if not args.watch:
            return 0
        time.sleep(args.watch)
        print()


def cmd_leds(args, c):
    state = c.state()["state"]
    leds = state["leds"]
    if args.json:
        print(json.dumps({"leds": leds, "raw": state["leds_raw"]}, indent=2))
        return 0
    print("strip:      " + " ".join(f"{i + 1:>5}" for i in range(8)))
    for group in ("rec", "solo", "mute", "select"):
        print(f"  {group:<10}" + " ".join(f"{led_text(s[group]):>5}" for s in state["strips"]))
    groups = [("automation", p.AUTOMATION_BUTTONS), ("transport", TRANSPORT_LEDS),
              ("assignment", p.ASSIGNMENT_BUTTONS), ("modes", p.MODE_BUTTONS)]
    listed = set()
    for title, names in groups:
        print(f"{title}:")
        print("  " + "  ".join(f"{n}={led_text(leds.get(n, 0))}" for n in names))
        listed.update(names)
    other = {n: v for n, v in leds.items() if n not in listed and not re.match(r"(rec|solo|mute|select)\d$", n)}
    if other:
        print("other:\n  " + "  ".join(f"{n}={led_text(v)}" for n, v in other.items()))
    return 0


TRANSPORT_LEDS = ["rewind", "fast_fwd", "stop", "play", "record", "cycle", "click", "solo_global"]


def cmd_time(args, c):
    state = c.state()["state"]
    data = {"time": state["time_text"], "time_digits": state["time_digits"], "mode": time_mode(state),
            "assign": state["assign_text"], "assign_digits": state["assign_digits"]}
    emit(args, data, f"time:   {state['time_text']!r} ({time_mode(state)})\nassign: {state['assign_text']!r}")
    return 0


def cmd_press(args, c):
    try:
        note = p.button_note(args.button)
    except ValueError as exc:
        sys.exit(str(exc))
    if note in p.DESTRUCTIVE_BUTTONS:
        require_yes(args, p.button_name(note))
    mods = [m.strip() for m in (args.with_ or "").split(",") if m.strip()]
    reply = c.press(args.button, args.hold, mods)
    mod_text = "+".join(mods) + "+" if mods else ""
    emit(args, reply, f"pressed {mod_text}{reply['name']} (0x{reply['note']:02X}), held {reply['hold_ms']} ms")
    return 0


def cmd_fader(args, c):
    strip = strip_arg(args.strip)
    value, desc = parse_fader_value(args.value)
    cal = load_calibration_or_none()
    before = c.state()
    seq = before["seq"]
    old = before["state"]["master_fader14"] if strip == p.MASTER else before["state"]["strips"][strip]["fader14"]
    c.call("fader", strip=strip, value14=value, touch=not args.no_touch)
    echo = wait_echo(args, c, "fader", seq, strip=strip)
    after = c.state()["state"]
    new = after["master_fader14"] if strip == p.MASTER else after["strips"][strip]["fader14"]
    lcd_value = "" if strip == p.MASTER else after["strips"][strip]["value"]
    data = {"strip": strip_label(strip), "sent": value, "target": desc, "before": old, "after": new,
            "echo": not echo["timed_out"], "lcd_value": lcd_value,
            "after_db": cal.fader_to_db(new) if cal else None}
    db_text = f" ({format_db(cal.fader_to_db(new))} dB)" if cal else ""
    emit(args, data, f"fader {strip_label(strip)}: sent {value} ({desc}); host fader {old} -> {new}{db_text}"
                     + (f", LCD {lcd_value!r}" if lcd_value else "")
                     + ("" if not echo["timed_out"] else "  [no echo]"))
    return 0


def cmd_touch(args, c):
    strip = strip_arg(args.strip)
    on = args.state == "on"
    reply = c.call("touch", strip=strip, on=on)
    emit(args, reply, f"touch {strip_label(strip)} {'on' if on else 'off'}")
    return 0


def cmd_vpot(args, c):
    strip = strip_arg(args.strip, allow_master=False)
    seq = c.state()["seq"]
    reply = c.call("vpot", strip=strip, delta=args.delta)
    echo = c.wait("any", timeout_ms=min(args.timeout, 600), since=seq)
    state = c.state()["state"]["strips"][strip]
    data = {"strip": strip + 1, "delta": args.delta, "messages": len(reply["sent"]),
            "ring": state["vpot"], "value": state["value"], "echo": not echo["timed_out"]}
    ring = state["vpot"]
    emit(args, data, f"vpot {strip + 1} {args.delta:+d} in {len(reply['sent'])} message(s); "
                     f"value {state['value']!r}, ring {ring['mode_name']} pos {ring['pos']}"
                     f"{' centre' if ring['center'] else ''}")
    return 0


def cmd_vpush(args, c):
    strip = strip_arg(args.strip, allow_master=False)
    reply = c.call("vpush", strip=strip, hold_ms=args.hold)
    emit(args, reply, f"vpot {strip + 1} pushed")
    return 0


def cmd_jog(args, c):
    reply = c.call("jog", delta=args.delta)
    seq_state = c.state()["state"]
    emit(args, {**reply, "time": seq_state["time_text"]}, f"jog {args.delta:+d}; time {seq_state['time_text']!r}")
    return 0


def cmd_led_button(args, c, kind):
    """mute|solo|select|rec <strip> on|off|toggle."""
    strip = strip_arg(args.strip, allow_master=False)
    button = f"{kind}{strip + 1}"
    note = p.BUTTONS[button]
    if kind == "rec":
        require_yes(args, button)
    before = c.state()
    current = before["state"]["strips"][strip][kind]
    want = args.state
    if want in ("on", "off") and (current == 127) == (want == "on"):
        emit(args, {"strip": strip + 1, kind: current, "pressed": False},
             f"{kind} {strip + 1}: already {led_text(current)}")
        return 0
    c.press(button, getattr(args, "hold", None))
    echo = wait_echo(args, c, "led", before["seq"], note=note)
    after = c.state()["state"]["strips"][strip][kind]
    emit(args, {"strip": strip + 1, kind: after, "pressed": True, "echo": not echo["timed_out"]},
         f"{kind} {strip + 1}: {led_text(current)} -> {led_text(after)}")
    return 0


def cmd_transport(args, c):
    button = TRANSPORT[args.action]
    if button == "record":
        require_yes(args, "record")
    echo = press_and_wait(args, c, button, "led", note=button)
    state = c.state()["state"]
    data = {"pressed": button, "echo": not echo["timed_out"],
            "leds": {n: state["leds"].get(n, 0) for n in TRANSPORT_LEDS}, "time": state["time_text"]}
    emit(args, data, f"pressed {button}\ntransport: {transport_text(state)}\ntime: {state['time_text']!r}")
    return 0


def cmd_auto(args, c):
    strip = strip_arg(args.strip, allow_master=False)
    state = c.state()["state"]
    if state["strips"][strip]["select"] != 127:
        echo = press_and_wait(args, c, f"select{strip + 1}", "led", note=0x18 + strip)
        state = c.state()["state"]
        if echo["timed_out"] and state["strips"][strip]["select"] != 127:
            warn(f"strip {strip + 1} did not report SELECT; automation buttons act on Logic's selection")
    target = AUTO_MODES[args.mode]
    leds = state["leds"]
    presses = []
    if target is None:
        lit = [n for n in p.AUTOMATION_BUTTONS[:5] if leds.get(n)]
        if not lit:
            presses = []
        elif leds.get("read_off"):
            presses = ["read_off"]
        else:
            presses = ["read_off", "read_off"]
    elif target == "read_off" and leds.get("read_off") == 127:
        presses = []
    else:
        presses = [target]
    for button in presses:
        press_and_wait(args, c, button, "led", note=button)
    state = c.state()["state"]
    data = {"strip": strip + 1, "mode": args.mode, "pressed": presses,
            "leds": {n: state["leds"].get(n, 0) for n in p.AUTOMATION_BUTTONS}}
    emit(args, data, f"auto {strip + 1} {args.mode}: pressed {', '.join(presses) or 'nothing'}\n"
                     f"automation: {automation_text(state)}")
    return 0


def cmd_mode(args, c):
    press_and_wait(args, c, args.mode, "lcd")
    time.sleep(0.1)
    state = c.state()["state"]
    emit(args, {"mode": args.mode, "assign": state["assign_text"], **lcd_json(state)},
         f"assignment {state['assign_text']!r}\n{render_lcd(state)}")
    return 0


def cmd_bank(args, c, what):
    button = f"{what}_{args.direction}"
    press_and_wait(args, c, button, "lcd", line="top")
    time.sleep(0.1)
    state = c.state()["state"]
    names = [s["name"] for s in state["strips"]]
    emit(args, {"pressed": button, "names": names, **lcd_json(state)},
         f"{button}: " + "  ".join(f"{i + 1} {n or '-'}" for i, n in enumerate(names)))
    return 0


def cmd_toggle_button(args, c, button):
    echo = press_and_wait(args, c, button, "any")
    time.sleep(0.1)
    state = c.state()["state"]
    data = {"pressed": button, "echo": not echo["timed_out"], "led": state["leds"].get(button, 0),
            "time": state["time_text"], "mode": time_mode(state), **lcd_json(state)}
    text = f"pressed {button}; LED {led_text(state['leds'].get(button, 0))}\n{render_lcd(state)}"
    if button == "smpte_beats":
        text += f"\ntime: {state['time_text']!r} ({time_mode(state)})"
    emit(args, data, text)
    return 0


def cmd_meters(args, c):
    while True:
        state = c.state()["state"]
        if args.json:
            print(json.dumps([{"idx": s["idx"], "name": s["name"], "level": s["meter_level"],
                               "clip": s["meter_clip"], "ts": s["meter_ts"]} for s in state["strips"]]))
        else:
            for s in state["strips"]:
                stamp = age(s["meter_ts"]) if s["meter_ts"] else "no data"
                print(f"{s['idx']} {s['name'][:6]:<7}{meter_bar(s):<18} {s['meter_level']:>2}  ({stamp})")
            if not any(s["meter_ts"] for s in state["strips"]):
                print("no meter data yet: Logic sends meters only while the transport runs or input is live")
        if not args.watch:
            return 0
        time.sleep(args.watch)
        print()


def cmd_calibrate(args, c):
    strip = strip_arg(args.strip, allow_master=False)
    steps = max(3, args.steps)
    state = c.state()["state"]
    original = state["strips"][strip]["fader14"]
    if state["handshake"] != "confirmed":
        warn("handshake is not confirmed; Logic may not be listening (continuing anyway)")
    pairs = []
    c.call("touch", strip=strip, on=True)
    try:
        for i in range(steps):
            value = round(i * 16383 / (steps - 1))
            seq = c.state()["seq"]
            c.call("pb", strip=strip, value14=value)
            c.wait("lcd", timeout_ms=args.settle, since=seq, strip=strip, line="bottom")
            # The first LCD write after a move is often the "Volume" label; the value follows.
            time.sleep(0.15)
            cell = c.state()["state"]["strips"][strip]["value"]
            try:
                db = parse_db_text(cell)
            except ValueError:
                warn(f"  {value:>5}: LCD says {cell!r}, not a level — skipped")
                continue
            pairs.append([value, db])
            if not args.json:
                print(f"  {value:>5}  {format_db(db):>6}   (LCD {cell!r})")
    finally:
        c.call("pb", strip=strip, value14=original)
        c.call("touch", strip=strip, on=False)
    if len([d for _, d in pairs if d is not None]) < 2:
        sys.exit("calibration failed: fewer than two readable dB values came back. Is the fader bank on a track, "
                 "is the assignment on Track/Pan so the LCD shows the fader level while touched?")
    pairs.sort()
    CALIBRATION.write_text(json.dumps({
        "created": datetime.now().isoformat(timespec="seconds"), "strip": strip + 1, "steps": steps,
        "pairs": pairs}, indent=1))
    emit(args, {"path": str(CALIBRATION), "pairs": pairs},
         f"wrote {len(pairs)} points to {CALIBRATION}; `fader <strip> <dB>` now works.")
    return 0


def cmd_watch(args, c):
    seq = c.state()["seq"]
    end = time.monotonic() + args.seconds if args.seconds else None
    try:
        while end is None or time.monotonic() < end:
            c.wait("any", timeout_ms=1000, since=seq)
            for ev in c.events(since=seq, max=500)["events"]:
                seq = ev["seq"]
                if args.json:
                    print(json.dumps(ev), flush=True)
                else:
                    stamp = datetime.fromtimestamp(ev["ts"]).strftime("%H:%M:%S.%f")[:-3]
                    print(f"{stamp}  {ev['dir']:<3}  {p.describe_event(ev)}", flush=True)
    except KeyboardInterrupt:
        pass
    return 0


def cmd_raw(args, c):
    if args.kind == "note":
        reply = c.call("note", n=args.a, vel=int(args.b) if args.b is not None else 0x7F)
    elif args.kind == "cc":
        if args.b is None:
            sys.exit("raw cc needs <controller> <value>")
        reply = c.call("cc", n=int(args.a, 0), v=int(args.b, 0))
    elif args.kind == "pb":
        if args.b is None:
            sys.exit("raw pb needs <strip|master> <value14>")
        reply = c.call("pb", strip=strip_arg(args.a), value14=int(args.b, 0))
    else:
        data = [int(h, 16) for h in [args.a] + ([args.b] if args.b else []) + args.rest]
        reply = c.call("sysex", bytes=data)
    emit(args, reply, "sent: " + "; ".join(f"{p.describe_outgoing(m)} [{p.hexstr(m)}]" for m in reply["sent"]))
    return 0


# --- argument parsing -------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(
        prog="mcuctl", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--run-dir", help="daemon socket/pidfile/log dir (default kit/mcu/run or $MCU_RUN_DIR)")
    parser.add_argument("--timeout", type=int, default=1500, help="ms to wait for Logic's echo (default 1500)")

    # --json is accepted on either side of the subcommand (same trick as logic.py).
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="machine-readable output")
    hold = argparse.ArgumentParser(add_help=False)
    hold.add_argument("--hold", type=int, default=None, help="ms to hold the button (default 40)")
    yes = argparse.ArgumentParser(add_help=False)
    yes.add_argument("--yes", action="store_true", help="confirm a destructive action")

    sub = parser.add_subparsers(
        dest="command", required=True, metavar="command",
        parser_class=lambda **kw: argparse.ArgumentParser(parents=[common] + kw.pop("extra", []), **kw))

    d = sub.add_parser("daemon", help="start | stop | status | log the background daemon")
    d.add_argument("action", choices=["start", "stop", "status", "log"],
                   help="start opens the MIDI ports in the background; log tails daemon.log")
    d.add_argument("--port-name", default=daemon_mod.DEFAULT_PORT_NAME,
                   help='virtual MIDI port name (default "Claude MCU")')
    d.add_argument("--device-id", default=f"0x{p.DEVICE_ID:02X}",
                   help="Mackie device id (default 0x14; tests use 0x7E so Logic ignores them)")
    d.add_argument("--verbose", action="store_true", help="log every MIDI message")
    d.add_argument("--lines", type=int, default=40, help="for log: how many lines")

    sub.add_parser("status", help="one screen: daemon, handshake, transport, display, strip names")
    sub.add_parser("lcd", help="both LCD lines with the 8 strip columns")
    s = sub.add_parser("strips", help="table: name, value, fader, LEDs, V-Pot ring, meter per strip")
    s.add_argument("--watch", type=float, metavar="SECONDS", help="refresh every N seconds")
    sub.add_parser("leds", help="every known LED, grouped")
    sub.add_parser("time", help="decoded timecode and assignment displays")

    pr = sub.add_parser("press", help="press a button by name (play, bank_left, mute3, f1, 0x2E ...)",
                        extra=[hold, yes])
    pr.add_argument("button", help="name (play, bank_left, mute3, f1), alias (ff, rew, sel3) or 0xNN")
    pr.add_argument("--with", dest="with_", metavar="MODS", help="hold modifiers: shift,option,control,alt")

    f = sub.add_parser("fader", help="move a fader: raw:NNNN, pct:NN or a dB value (needs calibrate)")
    f.add_argument("strip", help="1-8 or master")
    f.add_argument("value", help="raw:0-16383, pct:0-100, or dB like -6, 0, +3, -inf")
    f.add_argument("--no-touch", action="store_true", help="send the position without touch on/off around it")

    t = sub.add_parser("touch", help="fader touch on/off (Touch/Latch automation, LCD shows the level)")
    t.add_argument("strip", help="1-8 or master")
    t.add_argument("state", choices=["on", "off"], help="on = finger on the fader, off = release")

    v = sub.add_parser("vpot", help="turn a V-Pot by N ticks (negative = counter-clockwise)")
    v.add_argument("strip", help="1-8")
    v.add_argument("delta", type=int, help="ticks, e.g. 3 or -5 (sent as <=15-tick messages)")
    vp = sub.add_parser("vpush", help="push a V-Pot (usually resets the parameter)", extra=[hold])
    vp.add_argument("strip", help="1-8")
    j = sub.add_parser("jog", help="turn the jog wheel by N clicks (negative = backwards)")
    j.add_argument("delta", type=int, help="clicks, e.g. 4 or -4")

    for kind, text in (("mute", "mute"), ("solo", "solo"), ("select", "select"), ("rec", "record-arm (needs --yes)")):
        b = sub.add_parser(kind, help=f"{text} a strip: on | off | toggle (on/off press only if needed)",
                           extra=[hold, yes])
        b.add_argument("strip", help="1-8")
        b.add_argument("state", choices=["on", "off", "toggle"],
                       help="on/off read the LED first and press only when needed")

    tr = sub.add_parser("transport", help="play | stop | rewind | ff | record (record needs --yes)", extra=[hold, yes])
    tr.add_argument("action", type=transport_action, metavar="{play,stop,rewind,ff,record}",
                    help="rew, fast_fwd and rec are accepted too")

    a = sub.add_parser("auto", help="automation mode of a strip: read | write | trim | touch | latch | off", extra=[hold])
    a.add_argument("strip", help="1-8")
    a.add_argument("mode", choices=list(AUTO_MODES),
                   help="off presses READ/OFF until no automation LED is lit (Logic toggles Read/Off)")

    m = sub.add_parser("mode", help="V-Pot assignment: track | send | pan | plugin | eq | instrument", extra=[hold])
    m.add_argument("mode", choices=p.ASSIGNMENT_BUTTONS, help="what the V-Pots control; the LCD follows")

    bk = sub.add_parser("bank", help="bank left | right (8 tracks) and print the new names", extra=[hold])
    bk.add_argument("direction", choices=["left", "right"], help="previous/next 8 tracks")
    ch = sub.add_parser("channel", help="channel left | right (1 track) and print the new names", extra=[hold])
    ch.add_argument("direction", choices=["left", "right"], help="shift the bank by one track")

    sub.add_parser("flip", help="press FLIP (swap faders and V-Pots) and print the LCD", extra=[hold])
    sub.add_parser("name-value", help="press NAME/VALUE and print the LCD", extra=[hold])
    sub.add_parser("smpte-beats", help="press SMPTE/BEATS and print the time display", extra=[hold])

    me = sub.add_parser("meters", help="per-strip level bars (Logic sends meters only while running)")
    me.add_argument("--watch", type=float, metavar="SECONDS", help="refresh every N seconds")

    cal = sub.add_parser("calibrate", help="sweep a fader, read dB off the LCD, write calibration.json")
    cal.add_argument("strip", help="1-8; the bank must show a track with a volume fader")
    cal.add_argument("--steps", type=int, default=33, help="points across 0-16383 (default 33)")
    cal.add_argument("--settle", type=int, default=120, help="ms to wait for the LCD after each step (default 120)")

    w = sub.add_parser("watch", help="stream decoded host messages (and what we send) live")
    w.add_argument("--seconds", type=float, default=0, help="stop after N seconds (default: until Ctrl-C)")

    r = sub.add_parser("raw", help="primitives: note <n> [vel] | cc <n> <v> | pb <strip> <v14> | sysex <hex...>")
    r.add_argument("kind", choices=["note", "cc", "pb", "sysex"],
                   help="note <name|0xNN> [vel] | cc <num> <val> | pb <strip|master> <0-16383> | "
                        "sysex <hex bytes> (header added unless it starts with F0)")
    r.add_argument("a", help="note name/number, controller, strip, or first sysex byte")
    r.add_argument("b", nargs="?", help="velocity, value, value14, or next sysex byte")
    r.add_argument("rest", nargs="*", help="more sysex bytes (hex)")
    return parser


def dispatch(args, run_dir):
    if args.command == "daemon":
        return cmd_daemon(args, run_dir)
    c = Client(run_dir)
    if args.command == "status":
        return cmd_status(args, c, run_dir)
    if args.command == "lcd":
        return cmd_lcd(args, c)
    if args.command == "strips":
        return cmd_strips(args, c)
    if args.command == "leds":
        return cmd_leds(args, c)
    if args.command == "time":
        return cmd_time(args, c)
    if args.command == "press":
        return cmd_press(args, c)
    if args.command == "fader":
        return cmd_fader(args, c)
    if args.command == "touch":
        return cmd_touch(args, c)
    if args.command == "vpot":
        return cmd_vpot(args, c)
    if args.command == "vpush":
        return cmd_vpush(args, c)
    if args.command == "jog":
        return cmd_jog(args, c)
    if args.command in ("mute", "solo", "select", "rec"):
        return cmd_led_button(args, c, args.command)
    if args.command == "transport":
        return cmd_transport(args, c)
    if args.command == "auto":
        return cmd_auto(args, c)
    if args.command == "mode":
        return cmd_mode(args, c)
    if args.command in ("bank", "channel"):
        return cmd_bank(args, c, args.command)
    if args.command in ("flip", "name-value", "smpte-beats"):
        return cmd_toggle_button(args, c, args.command.replace("-", "_"))
    if args.command == "meters":
        return cmd_meters(args, c)
    if args.command == "calibrate":
        return cmd_calibrate(args, c)
    if args.command == "watch":
        return cmd_watch(args, c)
    if args.command == "raw":
        return cmd_raw(args, c)
    sys.exit(f"unhandled command {args.command}")


def main():
    args = build_parser().parse_args()
    run_dir = daemon_mod.run_dir_from(args.run_dir)
    try:
        return dispatch(args, run_dir)
    except DaemonNotRunning as exc:
        warn(f"daemon not running ({exc}).\nStart it with:  {LAUNCHER} daemon start")
        return 2
    except DaemonError as exc:
        warn(f"error: {exc}")
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
