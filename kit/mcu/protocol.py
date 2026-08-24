"""Mackie Control Universal wire format: constants, encoders, decoders. No I/O.

Everything in here is pure, so the protocol tests run without rtmidi or a
MIDI port. Only daemon.py and fake_logic.py import rtmidi.

Summary (device id 0x14 = Mackie Control; MIDI channel 1 unless noted):

    device -> host (we send)           host -> device (we decode)
    button    90 nn 7F, release 90 nn 00 LED      90 nn vv  (00 off, 01 blink, 7F on)
    fader     Ex ll mm (x = strip 0-7,   fader    Ex ll mm  (motor feedback, same coding)
              8 = master; 14-bit, LSB first)
    touch     90 68+s 7F / 00 (master 70)
    v-pot     B0 10+s vv (01..0F cw,     ring     B0 30+s vv (0x40 centre LED, bits 4-5 mode,
              41..4F ccw)                                     bits 0-3 position)
    v-pot push 90 20+s                   7-seg    B0 40..49 vv (49 = leftmost time digit,
    jog       B0 3C 01 (cw) / 41 (ccw)                         40 = rightmost), 4B/4A =
                                                               assignment left/right digit
                                         meter    D0 vv  (strip<<4 | level; E clip on, F clip off)
                                         LCD      F0 00 00 66 14 12 <offset> <text> F7
                                                  (112 cells: 0-55 top line, 56-111 bottom)
    handshake: host 00 -> device 01 <serial7> <challenge4> -> host 02 <serial7> <response4>
               -> device 03 <serial7>.   Version: host 13 00 -> device 14 "V1.0".

Strips are 0-7 in this module and the daemon (8 = master); the CLI numbers them
1-8 like the hardware.
"""

import difflib
import re

DEVICE_ID = 0x14
SYSEX_HDR = [0xF0, 0x00, 0x00, 0x66, DEVICE_ID]
MANUFACTURER = [0x00, 0x00, 0x66]

SERIAL = b"CLAUDE1"
CHALLENGE = [0x01, 0x02, 0x03, 0x04]
VERSION = "V1.0"

LCD_CELLS = 112
LCD_LINE = 56
STRIP_WIDTH = 7
MASTER = 8

# --- buttons -----------------------------------------------------------------

BUTTONS = {}
for _i in range(8):
    BUTTONS[f"rec{_i + 1}"] = 0x00 + _i
    BUTTONS[f"solo{_i + 1}"] = 0x08 + _i
    BUTTONS[f"mute{_i + 1}"] = 0x10 + _i
    BUTTONS[f"select{_i + 1}"] = 0x18 + _i
    BUTTONS[f"vsel{_i + 1}"] = 0x20 + _i
    BUTTONS[f"f{_i + 1}"] = 0x36 + _i
    BUTTONS[f"fader_touch{_i + 1}"] = 0x68 + _i
BUTTONS.update({
    "track": 0x28, "send": 0x29, "pan": 0x2A, "plugin": 0x2B, "eq": 0x2C, "instrument": 0x2D,
    "bank_left": 0x2E, "bank_right": 0x2F, "channel_left": 0x30, "channel_right": 0x31,
    "flip": 0x32, "global_view": 0x33, "name_value": 0x34, "smpte_beats": 0x35,
    "midi_tracks": 0x3E, "inputs": 0x3F, "audio_tracks": 0x40, "audio_instrument": 0x41,
    "aux": 0x42, "busses": 0x43, "outputs": 0x44, "user": 0x45,
    "shift": 0x46, "option": 0x47, "control": 0x48, "alt": 0x49,
    "read_off": 0x4A, "write": 0x4B, "trim": 0x4C, "touch": 0x4D, "latch": 0x4E, "group": 0x4F,
    "save": 0x50, "undo": 0x51, "cancel": 0x52, "enter": 0x53,
    "marker": 0x54, "nudge": 0x55, "cycle": 0x56, "drop": 0x57, "replace": 0x58,
    "click": 0x59, "solo_global": 0x5A,
    "rewind": 0x5B, "fast_fwd": 0x5C, "stop": 0x5D, "play": 0x5E, "record": 0x5F,
    "up": 0x60, "down": 0x61, "left": 0x62, "right": 0x63, "zoom": 0x64, "scrub": 0x65,
    "user_a": 0x66, "user_b": 0x67,
    "fader_touch_master": 0x70,
    "smpte_led": 0x71, "beats_led": 0x72, "rude_solo_led": 0x73, "relay_click": 0x76,
})
BUTTON_NAMES = {note: name for name, note in BUTTONS.items()}

# Spellings people actually type. Resolved after normalisation, before lookup.
ALIASES = {
    "ff": "fast_fwd", "fwd": "fast_fwd", "fastforward": "fast_fwd", "fast_forward": "fast_fwd",
    "rew": "rewind", "rwd": "rewind", "read": "read_off", "loop": "cycle", "global": "global_view",
    "chan_left": "channel_left", "chan_right": "channel_right", "plug_in": "plugin",
    "inst": "instrument", "touch_master": "fader_touch_master", "fader_touch_m": "fader_touch_master",
    "smpte": "smpte_beats", "beats": "smpte_beats", "name": "name_value", "value": "name_value",
}
for _i in range(8):
    ALIASES[f"sel{_i + 1}"] = f"select{_i + 1}"
    ALIASES[f"vpush{_i + 1}"] = f"vsel{_i + 1}"
    ALIASES[f"arm{_i + 1}"] = f"rec{_i + 1}"
    ALIASES[f"touch{_i + 1}"] = f"fader_touch{_i + 1}"

DESTRUCTIVE_BUTTONS = {BUTTONS[f"rec{i + 1}"] for i in range(8)} | {
    BUTTONS["record"], BUTTONS["replace"], BUTTONS["drop"],
}

TRANSPORT_BUTTONS = ["rewind", "fast_fwd", "stop", "play", "record"]
AUTOMATION_BUTTONS = ["read_off", "write", "trim", "touch", "latch", "group"]
ASSIGNMENT_BUTTONS = ["track", "send", "pan", "plugin", "eq", "instrument"]
MODE_BUTTONS = ["flip", "global_view", "name_value", "smpte_beats", "cycle", "click",
                "solo_global", "marker", "nudge", "drop", "replace", "zoom", "scrub",
                "midi_tracks", "inputs", "audio_tracks", "audio_instrument", "aux",
                "busses", "outputs", "user", "smpte_led", "beats_led", "rude_solo_led"]

VPOT_MODES = ["single", "boost_cut", "wrap", "spread"]
LED_STATES = {0x00: "off", 0x01: "blink", 0x7F: "on"}


def normalise_button(text):
    """'Bank Left', 'bank-left', 'BANK_LEFT' -> 'bank_left'."""
    key = re.sub(r"[\s\-]+", "_", str(text).strip().lower())
    return re.sub(r"_+", "_", key).strip("_")


def button_note(name_or_hex):
    """Note number for a button name, alias, '0x2E' or decimal '46'.

    Raises ValueError naming close matches when the name is unknown.
    """
    if isinstance(name_or_hex, int):
        note = name_or_hex
        if 0 <= note <= 127:
            return note
        raise ValueError(f"button number {note} out of range 0-127")
    text = str(name_or_hex).strip()
    key = normalise_button(text)
    if re.fullmatch(r"0x[0-9a-f]+", key):
        note = int(key, 16)
    elif key.isdigit():
        note = int(key)
    else:
        note = None
    if note is not None:
        if 0 <= note <= 127:
            return note
        raise ValueError(f"button number {text!r} out of range 0-127 (0x00-0x7F)")
    key = ALIASES.get(key, key)
    if key in BUTTONS:
        return BUTTONS[key]
    close = difflib.get_close_matches(key, list(BUTTONS) + list(ALIASES), n=5, cutoff=0.6)
    hint = f" Close matches: {', '.join(close)}." if close else ""
    raise ValueError(f"unknown button {text!r}.{hint} Names are like play, bank_left, mute3, 0x2E.")


def button_name(note):
    return BUTTON_NAMES.get(note, f"0x{note:02X}")


def led_state_name(value):
    return LED_STATES.get(value, f"{value}")


# --- encoders (device -> host) -----------------------------------------------

def _u7(value, what="value"):
    value = int(value)
    if not 0 <= value <= 127:
        raise ValueError(f"{what} {value} out of range 0-127")
    return value


def _strip(strip, allow_master=True):
    strip = int(strip)
    top = MASTER if allow_master else 7
    if not 0 <= strip <= top:
        raise ValueError(f"strip {strip} out of range 0-{top}")
    return strip


def note_on(note, vel=0x7F):
    return [0x90, _u7(note, "note"), _u7(vel, "velocity")]


def note_off(note):
    return [0x90, _u7(note, "note"), 0x00]


def cc(num, val):
    return [0xB0, _u7(num, "controller"), _u7(val, "value")]


def pitch_bend(strip, value14):
    """Fader position for strip 0-7 or 8 (master). 14-bit, LSB first."""
    strip = _strip(strip)
    value14 = max(0, min(16383, int(value14)))
    return [0xE0 + strip, value14 & 0x7F, (value14 >> 7) & 0x7F]


def pitch_bend_value(message):
    return (message[2] << 7) | message[1]


def fader_touch(strip, on=True):
    strip = _strip(strip)
    note = 0x70 if strip == MASTER else 0x68 + strip
    return note_on(note, 0x7F if on else 0x00)


def vpot_turn(strip, delta):
    """One CC per <=15 ticks: delta 20 -> [0x0F, 0x05]; delta -3 -> [0x43]."""
    strip = _strip(strip, allow_master=False)
    delta = int(delta)
    out = []
    remaining = abs(delta)
    while remaining > 0:
        n = min(15, remaining)
        out.append([0xB0, 0x10 + strip, n if delta > 0 else 0x40 | n])
        remaining -= n
    return out


def vpot_push(strip):
    return note_on(0x20 + _strip(strip, allow_master=False))


def jog(delta):
    """One CC per click: 3C 01 clockwise, 3C 41 counter-clockwise."""
    delta = int(delta)
    return [[0xB0, 0x3C, 0x01 if delta > 0 else 0x41] for _ in range(abs(delta))]


def sysex(body, device_id=DEVICE_ID):
    return [0xF0] + MANUFACTURER + [device_id] + [_u7(b, "sysex byte") for b in body] + [0xF7]


def _serial7(serial):
    serial = list(bytes(serial))
    if len(serial) != 7 or any(b > 0x7F for b in serial):
        raise ValueError("serial must be 7 bytes, each < 0x80")
    return serial


def host_connection_query(serial=SERIAL, challenge=CHALLENGE, device_id=DEVICE_ID):
    """Handshake step 2: F0 00 00 66 14 01 <serial 7> <challenge 4> F7."""
    challenge = [_u7(c, "challenge byte") for c in challenge]
    if len(challenge) != 4:
        raise ValueError("challenge must be 4 bytes")
    return sysex([0x01] + _serial7(serial) + challenge, device_id)


def host_connection_confirm(serial=SERIAL, device_id=DEVICE_ID):
    """Handshake step 4: F0 00 00 66 14 03 <serial 7> F7."""
    return sysex([0x03] + _serial7(serial), device_id)


def host_connection_error(serial=SERIAL, device_id=DEVICE_ID):
    return sysex([0x04] + _serial7(serial), device_id)


def version_reply(version=VERSION, device_id=DEVICE_ID):
    """Reply to 13 00: F0 00 00 66 14 14 'V1.0' F7."""
    return sysex([0x14] + list(version.encode("ascii")), device_id)


def device_query(device_id=DEVICE_ID):
    """Handshake step 1 (a host sends this; fake_logic uses it)."""
    return sysex([0x00], device_id)


def challenge_response(challenge):
    """The published Logic Control response formula. We never check it;
    fake_logic uses it to look like a real host."""
    l1, l2, l3, l4 = challenge
    return [
        0x7F & (l1 + (l2 ^ 0x0A) - l4),
        0x7F & ((l3 >> 4) ^ (l1 + l4)),
        0x7F & ((l4 - (l3 << 2)) ^ (l1 | l2)),
        0x7F & (l2 - l3 + (0xF0 ^ (l4 << 4))),
    ]


def host_connection_reply(serial, response, device_id=DEVICE_ID):
    """Handshake step 3 (host side)."""
    return sysex([0x02] + _serial7(serial) + [_u7(r) for r in response], device_id)


def lcd_write(offset, text, device_id=DEVICE_ID):
    """Host-side LCD write (fake_logic / tests)."""
    if not 0 <= offset < LCD_CELLS:
        raise ValueError(f"LCD offset {offset} out of range 0-{LCD_CELLS - 1}")
    data = [ord(ch) & 0x7F for ch in text]
    return sysex([0x12, offset] + data, device_id)


def seg7_encode(char, dot=False):
    """Inverse of seg7_char, for hosts/tests writing the 7-segment display."""
    code = ord(char) & 0x7F
    if 0x40 <= code < 0x60:
        value = code - 0x40
    else:
        value = code & 0x3F
    return value | (0x40 if dot else 0)


# --- decoders (host -> device) -----------------------------------------------

def seg7_char(v):
    """(char, dot) for one 7-segment display byte."""
    code = v & 0x3F
    char = chr(0x40 + code) if code < 0x20 else chr(code)
    return char, bool(v & 0x40)


def decode_vpot_ring(v):
    mode = (v & 0x30) >> 4
    return {
        "center": bool(v & 0x40),
        "mode": mode,
        "mode_name": VPOT_MODES[mode],
        "pos": v & 0x0F,
        "raw": v,
    }


def decode_meter(v):
    """{strip, level, clip}: level 0-12 (None for clip messages); clip True/False/None."""
    strip = (v >> 4) & 0x07
    code = v & 0x0F
    if code == 0x0E:
        return {"strip": strip, "level": None, "clip": True}
    if code == 0x0F:
        return {"strip": strip, "level": None, "clip": False}
    return {"strip": strip, "level": code, "clip": None}


_SYSEX_MISC = {
    0x0A: "transport_click", 0x0B: "lcd_saver", 0x0C: "touchless_faders",
    0x0E: "fader_touch_sensitivity",
}


def _text(data):
    return "".join(chr(b) if 0x20 <= b < 0x7F else "?" for b in data)


def _decode_sysex(m, device_id):
    if len(m) < 6 or m[-1] != 0xF7:
        return {"kind": "sysex_unknown", "bytes": list(m)}
    if m[1:4] != MANUFACTURER:
        return {"kind": "sysex_unknown", "bytes": list(m)}
    if m[4] != device_id:
        return {"kind": "sysex_unknown", "bytes": list(m), "device_id": m[4]}
    mid = m[5]
    data = m[6:-1]
    if mid == 0x00:
        return {"kind": "sysex_device_query"}
    if mid == 0x02:
        return {
            "kind": "sysex_host_reply",
            "serial": _text(data[:7]),
            "serial_bytes": data[:7],
            "response": data[7:11],
        }
    if mid == 0x13:
        return {"kind": "sysex_version_request"}
    if mid == 0x0F and data[:1] == [0x7F]:
        return {"kind": "sysex_offline"}
    if mid in _SYSEX_MISC:
        return {"kind": "sysex_misc", "id": mid, "name": _SYSEX_MISC[mid], "data": data}
    if mid == 0x10:
        digits = [seg7_char(b) for b in data]
        return {"kind": "sysex_time_text", "text": "".join(c for c, _ in digits), "digits": digits}
    if mid == 0x11:
        digits = [seg7_char(b) for b in data]
        return {"kind": "sysex_assign_text", "text": "".join(c for c, _ in digits), "digits": digits}
    if mid == 0x12:
        if not data:
            return {"kind": "sysex_unknown", "bytes": list(m)}
        return {"kind": "lcd", "offset": data[0], "text": "".join(chr(b) for b in data[1:])}
    if mid == 0x20 and len(data) >= 2:
        return {"kind": "sysex_meter_mode", "strip": data[0], "mode": data[1]}
    if mid == 0x21 and len(data) >= 1:
        return {"kind": "sysex_global_meter", "on": bool(data[0])}
    if mid == 0x61:
        return {"kind": "sysex_faders_min"}
    if mid == 0x62:
        return {"kind": "sysex_leds_off"}
    if mid == 0x63:
        return {"kind": "sysex_reset"}
    return {"kind": "sysex_unknown", "bytes": list(m), "id": mid}


def decode(message, device_id=DEVICE_ID):
    """Decode one host->device MIDI message into an event dict. Never raises."""
    try:
        m = [int(b) & 0xFF for b in message]
    except (TypeError, ValueError):
        return {"kind": "unknown", "bytes": []}
    if not m:
        return {"kind": "unknown", "bytes": []}
    status = m[0]
    try:
        if status == 0xF0:
            return _decode_sysex(m, device_id)
        if status in (0x90, 0x80) and len(m) >= 3:
            note, vel = m[1], m[2] if status == 0x90 else 0
            return {"kind": "led", "note": note, "state": vel, "name": button_name(note),
                    "state_name": led_state_name(vel)}
        if 0xE0 <= status <= 0xE8 and len(m) >= 3:
            return {"kind": "fader", "strip": status - 0xE0, "value14": pitch_bend_value(m)}
        if status == 0xB0 and len(m) >= 3:
            num, val = m[1], m[2]
            if 0x30 <= num <= 0x37:
                ring = decode_vpot_ring(val)
                ring.update({"kind": "vpot_ring", "strip": num - 0x30})
                return ring
            if 0x40 <= num <= 0x49:
                char, dot = seg7_char(val)
                return {"kind": "time_digit", "index": 0x49 - num, "char": char, "dot": dot, "raw": val}
            if num in (0x4A, 0x4B):
                char, dot = seg7_char(val)
                return {"kind": "assign_digit", "index": 0x4B - num, "char": char, "dot": dot, "raw": val}
            return {"kind": "unknown", "bytes": m, "hint": f"cc 0x{num:02X} = {val}"}
        if status == 0xD0 and len(m) >= 2:
            meter = decode_meter(m[1])
            meter["kind"] = "meter"
            return meter
    except Exception as exc:  # noqa: BLE001 - decoding must never take the daemon down
        return {"kind": "unknown", "bytes": m, "error": str(exc)}
    return {"kind": "unknown", "bytes": m}


# --- human descriptions -------------------------------------------------------

def hexstr(data):
    return " ".join(f"{b:02X}" for b in data)


def describe_event(ev):
    """One line for a decoded host event (used by `watch`)."""
    k = ev.get("kind", "?")
    if k == "led":
        return f"led {ev['name']} (0x{ev['note']:02X}) {ev.get('state_name', ev['state'])}"
    if k == "fader":
        who = "master" if ev["strip"] == MASTER else f"strip {ev['strip'] + 1}"
        return f"fader {who} = {ev['value14']}"
    if k == "vpot_ring":
        c = " centre" if ev["center"] else ""
        return f"vpot ring strip {ev['strip'] + 1} {ev['mode_name']} pos {ev['pos']}{c}"
    if k == "time_digit":
        return f"time digit {ev['index']} = {ev['char']!r}{'.' if ev['dot'] else ''}"
    if k == "assign_digit":
        return f"assign digit {ev['index']} = {ev['char']!r}{'.' if ev['dot'] else ''}"
    if k == "lcd":
        line = "top" if ev["offset"] < LCD_LINE else "bottom"
        return f"lcd @{ev['offset']} ({line}) {ev['text']!r}"
    if k == "meter":
        if ev["clip"] is not None:
            return f"meter strip {ev['strip'] + 1} clip {'on' if ev['clip'] else 'off'}"
        return f"meter strip {ev['strip'] + 1} level {ev['level']}"
    if k == "sysex_host_reply":
        return f"handshake: host connection reply serial={ev['serial']!r} response={ev['response']}"
    if k == "sysex_misc":
        return f"sysex {ev['name']} {ev['data']}"
    if k in ("sysex_unknown", "unknown"):
        extra = f" (device id 0x{ev['device_id']:02X})" if "device_id" in ev else ""
        return f"{k}{extra}: {hexstr(ev.get('bytes', []))}"
    if k == "sent":
        return f"sent {ev.get('desc', '')}: {hexstr(ev.get('bytes', []))}"
    rest = {key: val for key, val in ev.items() if key not in ("kind", "seq", "ts", "dir")}
    return f"{k} {rest}" if rest else k


def describe_outgoing(message):
    """One line for a device->host message we sent."""
    m = list(message)
    if not m:
        return "empty"
    s = m[0]
    if s == 0x90 and len(m) >= 3:
        note, vel = m[1], m[2]
        if 0x68 <= note <= 0x70:
            who = "master" if note == 0x70 else f"strip {note - 0x68 + 1}"
            return f"touch {who} {'on' if vel else 'off'}"
        if 0x20 <= note <= 0x27:
            return f"vpot push strip {note - 0x20 + 1} {'down' if vel else 'up'}"
        return f"{'press' if vel else 'release'} {button_name(note)}"
    if 0xE0 <= s <= 0xE8 and len(m) >= 3:
        who = "master" if s == 0xE8 else f"strip {s - 0xE0 + 1}"
        return f"fader {who} -> {pitch_bend_value(m)}"
    if s == 0xB0 and len(m) >= 3:
        if 0x10 <= m[1] <= 0x17:
            n = m[2] & 0x0F
            return f"vpot strip {m[1] - 0x10 + 1} {'-' if m[2] & 0x40 else '+'}{n}"
        if m[1] == 0x3C:
            return f"jog {'ccw' if m[2] & 0x40 else 'cw'}"
        return f"cc 0x{m[1]:02X} = {m[2]}"
    if s == 0xF0 and len(m) >= 6:
        names = {0x01: "host connection query", 0x03: "host connection confirmation",
                 0x04: "host connection error", 0x14: "version reply"}
        return f"sysex {names.get(m[5], f'id 0x{m[5]:02X}')}"
    return f"raw {hexstr(m)}"
