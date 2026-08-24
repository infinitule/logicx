"""The model of what the host has told us: LCD, LEDs, faders, rings, meters, display.

State is plain data plus `apply(event, ts)` for the decoded events produced by
protocol.decode(). The daemon owns one State behind a lock; `snapshot()` is what
goes over the socket. Strips are 0-7 here (the CLI shows 1-8).
"""

import time
from dataclasses import dataclass, field

from . import protocol as p

HANDSHAKE_STAGES = ("none", "queried", "replied", "confirmed")


def _blank_ring():
    return {"center": False, "mode": 0, "mode_name": "single", "pos": 0, "raw": 0}


@dataclass
class Strip:
    index: int
    fader14: int = 0
    touched: bool = False
    rec: int = 0
    solo: int = 0
    mute: int = 0
    select: int = 0
    vpot: dict = field(default_factory=_blank_ring)
    meter_level: int = 0
    meter_clip: bool = False
    meter_ts: float | None = None

    def to_dict(self, name, value):
        return {
            "strip": self.index,
            "idx": self.index + 1,
            "name": name,
            "value": value,
            "fader14": self.fader14,
            "touched": self.touched,
            "rec": self.rec,
            "solo": self.solo,
            "mute": self.mute,
            "select": self.select,
            "vpot": dict(self.vpot),
            "meter_level": self.meter_level,
            "meter_clip": self.meter_clip,
            "meter_ts": self.meter_ts,
        }


class State:
    def __init__(self):
        self.strips = [Strip(i) for i in range(8)]
        self.master_fader14 = 0
        self.master_touched = False
        self.lcd = bytearray(b" " * p.LCD_CELLS)
        self.leds = {}
        self.time_digits = [(" ", False)] * 10
        self.assign_digits = [(" ", False)] * 2
        self.handshake = "none"
        self.handshake_ts = {}
        self.online = False
        self.meter_modes = {}
        self.global_meter = None
        self.misc = {}
        self.last_host_ts = None
        self.counters = {"total": 0}

    # --- derived views ------------------------------------------------------

    @property
    def lcd_top(self):
        return self.lcd[:p.LCD_LINE].decode("latin-1")

    @property
    def lcd_bottom(self):
        return self.lcd[p.LCD_LINE:].decode("latin-1")

    def strip_name(self, i):
        return self.lcd_top[i * p.STRIP_WIDTH:(i + 1) * p.STRIP_WIDTH].rstrip()

    def strip_value(self, i):
        return self.lcd_bottom[i * p.STRIP_WIDTH:(i + 1) * p.STRIP_WIDTH].strip()

    @staticmethod
    def _digits_text(digits):
        return "".join(ch + ("." if dot else "") for ch, dot in digits)

    @property
    def time_text(self):
        return self._digits_text(self.time_digits)

    @property
    def assign_text(self):
        return self._digits_text(self.assign_digits)

    @property
    def handshake_complete(self):
        return self.handshake == "confirmed"

    def led(self, note):
        return self.leds.get(note, 0)

    # --- mutation -----------------------------------------------------------

    def set_handshake(self, stage, ts=None):
        if stage not in HANDSHAKE_STAGES:
            raise ValueError(f"bad handshake stage {stage!r}")
        self.handshake = stage
        self.handshake_ts[stage] = ts if ts is not None else time.time()
        if stage == "confirmed":
            self.online = True

    def set_touched(self, strip, on):
        if strip == p.MASTER:
            self.master_touched = bool(on)
        else:
            self.strips[strip].touched = bool(on)

    def reset_meters(self):
        for s in self.strips:
            s.meter_level, s.meter_clip = 0, False

    def _set_led(self, note, value):
        self.leds[note] = value
        if note < 0x20:
            strip = self.strips[note & 0x07]
            attr = ("rec", "solo", "mute", "select")[note >> 3]
            setattr(strip, attr, value)

    def _write_lcd(self, offset, text):
        data = text.encode("latin-1", "replace")
        end = min(p.LCD_CELLS, offset + len(data))
        if 0 <= offset < p.LCD_CELLS:
            self.lcd[offset:end] = data[:end - offset]

    def _clear_lcd(self):
        self.lcd[:] = b" " * p.LCD_CELLS

    def _set_digits(self, target, digits, index=None):
        for i, (ch, dot) in enumerate(digits):
            j = i if index is None else index
            if 0 <= j < len(target):
                target[j] = (ch, dot)

    def apply(self, event, ts=None):
        """Fold one decoded host event into the model."""
        ts = ts if ts is not None else time.time()
        kind = event.get("kind", "unknown")
        self.last_host_ts = ts
        self.counters["total"] += 1
        self.counters[kind] = self.counters.get(kind, 0) + 1

        if kind == "led":
            self._set_led(event["note"], event["state"])
        elif kind == "fader":
            if event["strip"] == p.MASTER:
                self.master_fader14 = event["value14"]
            else:
                self.strips[event["strip"]].fader14 = event["value14"]
        elif kind == "vpot_ring":
            ring = {k: event[k] for k in ("center", "mode", "mode_name", "pos", "raw")}
            self.strips[event["strip"]].vpot = ring
        elif kind == "time_digit":
            self._set_digits(self.time_digits, [(event["char"], event["dot"])], event["index"])
        elif kind == "assign_digit":
            self._set_digits(self.assign_digits, [(event["char"], event["dot"])], event["index"])
        elif kind == "lcd":
            self._write_lcd(event["offset"], event["text"])
        elif kind == "meter":
            strip = self.strips[event["strip"]]
            if event["clip"] is not None:
                strip.meter_clip = event["clip"]
            else:
                strip.meter_level = event["level"]
            strip.meter_ts = ts
        elif kind == "sysex_device_query":
            self.set_handshake("queried", ts)
        elif kind == "sysex_host_reply":
            self.set_handshake("replied", ts)
        elif kind == "sysex_offline":
            self.online = False
        elif kind == "sysex_misc":
            self.misc[event["name"]] = event["data"]
        elif kind == "sysex_time_text":
            self._set_digits(self.time_digits, event["digits"])
        elif kind == "sysex_assign_text":
            self._set_digits(self.assign_digits, event["digits"])
        elif kind == "sysex_meter_mode":
            self.meter_modes[event["strip"]] = event["mode"]
        elif kind == "sysex_global_meter":
            self.global_meter = event["on"]
        elif kind == "sysex_faders_min":
            for s in self.strips:
                s.fader14 = 0
            self.master_fader14 = 0
        elif kind == "sysex_leds_off":
            for note in list(self.leds):
                self._set_led(note, 0)
        elif kind == "sysex_reset":
            self.apply({"kind": "sysex_faders_min"}, ts)
            self.apply({"kind": "sysex_leds_off"}, ts)
            self._clear_lcd()
            self.time_digits = [(" ", False)] * 10
            self.assign_digits = [(" ", False)] * 2
            for s in self.strips:
                s.vpot = _blank_ring()
            self.reset_meters()
            self.counters["total"] -= 2
        # sysex_version_request, sysex_unknown, unknown: counted only.

    # --- export -------------------------------------------------------------

    def snapshot(self):
        leds_named = {p.button_name(n): v for n, v in sorted(self.leds.items())}
        return {
            "strips": [s.to_dict(self.strip_name(i), self.strip_value(i)) for i, s in enumerate(self.strips)],
            "master_fader14": self.master_fader14,
            "master_touched": self.master_touched,
            "lcd": self.lcd.decode("latin-1"),
            "lcd_top": self.lcd_top,
            "lcd_bottom": self.lcd_bottom,
            "leds": leds_named,
            "leds_raw": {str(n): v for n, v in sorted(self.leds.items())},
            "time_digits": [list(d) for d in self.time_digits],
            "time_text": self.time_text,
            "assign_digits": [list(d) for d in self.assign_digits],
            "assign_text": self.assign_text,
            "handshake": self.handshake,
            "handshake_ts": dict(self.handshake_ts),
            "handshake_complete": self.handshake_complete,
            "online": self.online,
            "meter_modes": {str(k): v for k, v in self.meter_modes.items()},
            "global_meter": self.global_meter,
            "misc": dict(self.misc),
            "last_host_ts": self.last_host_ts,
            "counters": dict(self.counters),
        }
