#!/usr/bin/env python3
"""A stand-in Logic Pro: opens the daemon's ports from the host side and talks MCU back.

    python3 fake_logic.py --port-name "Claude MCU test" --device-id 0x7E --seconds 60

It answers the handshake, paints an 8-strip LCD with track names, echoes fader
moves (and shows the value in the LCD cell while the fader is touched, as
Logic does), toggles strip/transport/automation LEDs on button presses, moves
the 7-segment display and streams meters while "playing". Tests use the
FakeHost class in-process with `simulate=False` and drive the bytes themselves.

Never point this at a daemon named "Claude MCU" while the real Logic is
connected to it: the two hosts would fight over the same surface.
"""

import argparse
import math
import signal
import sys
import threading
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).absolute().parent.parent))
    __package__ = "mcu"

from . import protocol as p  # noqa: E402

TRACKS = ["Kick", "Snare", "HiHat", "Bass", "Gtr L", "Gtr R", "Keys", "Vox",
          "Synth", "Pad", "Strngs", "Brass", "FX", "Bus A", "Bus B", "Rev"]
MODE_ASSIGN = {"pan": "PN", "track": "TR", "send": "SE", "plugin": "PL", "eq": "EQ", "instrument": "IN"}


class PortError(Exception):
    pass


def fake_db(value14):
    """A plausible fader law for the fake: -inf at 0, 0 dB at 12288, +6 dB at the top."""
    if value14 <= 0:
        return None
    if value14 >= 12288:
        return 6.0 * (value14 - 12288) / (16383 - 12288)
    return -60.0 * (12288 - value14) / 12288


def format_db(db):
    if db is None:
        return "-inf"
    if abs(db) < 0.05:
        return "0.0"
    return f"{db:+.1f}" if db > 0 else f"{db:.1f}"


class FakeHost:
    def __init__(self, port_name, device_id=p.DEVICE_ID, auto_handshake=True, simulate=False, log=None):
        self.port_name = port_name
        self.device_id = device_id
        self.auto_handshake = auto_handshake
        self.simulate = simulate
        self.log = log or (lambda *_: None)
        self.received = []
        self.cond = threading.Condition()
        self.connected = False
        self.midi_in = None
        self.midi_out = None
        # simulation model
        self.bank = 0
        self.mode = "pan"
        self.faders = [12288] * len(TRACKS)
        self.pans = [0] * len(TRACKS)
        self.touched = set()
        self.mutes, self.solos, self.recs = set(), set(), set()
        self.selected = 0
        self.automation = ["read_off"] * len(TRACKS)
        self.playing = False
        self.recording = False
        self.beats_mode = True
        self.position = 0
        self.leds = {}
        self._stop = threading.Event()
        self._ticker = None

    # --- ports --------------------------------------------------------------

    def open(self, timeout=3.0):
        import rtmidi
        deadline = time.monotonic() + timeout
        while True:
            ins, outs = rtmidi.MidiIn(), rtmidi.MidiOut()
            in_idx = [i for i, n in enumerate(ins.get_ports()) if n == self.port_name]
            out_idx = [i for i, n in enumerate(outs.get_ports()) if n == self.port_name]
            if in_idx and out_idx:
                break
            del ins, outs
            if time.monotonic() > deadline:
                raise PortError(f"no MIDI ports named {self.port_name!r} (is the daemon running?)")
            time.sleep(0.1)
        self.midi_in = ins
        self.midi_in.ignore_types(sysex=False, timing=True, active_sense=True)
        self.midi_in.set_callback(self._on_midi)
        self.midi_in.open_port(in_idx[0], name="fake logic in")
        self.midi_out = outs
        self.midi_out.open_port(out_idx[0], name="fake logic out")
        time.sleep(0.1)  # let CoreMIDI finish wiring before the first message
        return self

    def close(self):
        self._stop.set()
        for port in (self.midi_in, self.midi_out):
            if port is not None:
                try:
                    port.close_port()
                except Exception:  # noqa: BLE001
                    pass
        self.midi_in = self.midi_out = None

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc):
        self.close()

    # --- receive ------------------------------------------------------------

    def _on_midi(self, event, data=None):
        try:
            message = list(event[0])
            with self.cond:
                self.received.append(message)
                self.cond.notify_all()
            self.log("device: " + p.describe_outgoing(message))
            self._react(message)
        except Exception as exc:  # noqa: BLE001
            self.log(f"fake host error: {exc!r}")

    def wait_for(self, predicate, timeout=2.0, skip=0):
        """First received message (after the first `skip`) matching predicate, or None."""
        deadline = time.monotonic() + timeout
        with self.cond:
            while True:
                for msg in self.received[skip:]:
                    if predicate(msg):
                        return msg
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self.cond.wait(remaining)

    def messages_since(self, index):
        with self.cond:
            return list(self.received[index:])

    @property
    def count(self):
        with self.cond:
            return len(self.received)

    def _react(self, m):
        s = m[0]
        if s == 0xF0 and len(m) >= 6 and m[1:4] == p.MANUFACTURER and m[4] == self.device_id:
            if m[5] == 0x01 and self.auto_handshake:
                serial, challenge = m[6:13], m[13:17]
                self.send(p.host_connection_reply(bytes(serial), p.challenge_response(challenge), self.device_id))
            elif m[5] == 0x03:
                self.connected = True
                if self.simulate:
                    self.paint_all()
            return
        if not self.simulate:
            return
        if s == 0x90 and len(m) >= 3:
            self._button(m[1], m[2])
        elif 0xE0 <= s <= 0xE8 and len(m) >= 3:
            self._fader_moved(s - 0xE0, p.pitch_bend_value(m))
        elif s == 0xB0 and 0x10 <= m[1] <= 0x17:
            strip = m[1] - 0x10
            n = m[2] & 0x0F
            track = self.bank + strip
            self.pans[track] = max(-64, min(63, self.pans[track] + (-n if m[2] & 0x40 else n)))
            self.paint_value(strip)
        elif s == 0xB0 and m[1] == 0x3C:
            self.position = max(0, self.position + (-1 if m[2] & 0x40 else 1))
            self.paint_time()

    # --- send helpers (host -> device) ---------------------------------------

    def send(self, message):
        if self.midi_out is None:
            raise PortError("fake host output port is not open")
        self.midi_out.send_message([int(b) for b in message])

    def device_query(self):
        self.send(p.device_query(self.device_id))

    def version_request(self):
        self.send(p.sysex([0x13, 0x00], self.device_id))

    def go_offline(self):
        self.send(p.sysex([0x0F, 0x7F], self.device_id))

    def sysex(self, body):
        self.send(p.sysex(body, self.device_id))

    def lcd(self, offset, text):
        self.send(p.lcd_write(offset, text, self.device_id))

    def led(self, note, state):
        self.leds[note] = state
        self.send(p.note_on(note, state))

    def fader(self, strip, value14):
        self.send(p.pitch_bend(strip, value14))

    def vpot_ring(self, strip, value):
        self.send(p.cc(0x30 + strip, value))

    def time_digits(self, text):
        """Write the 10-digit display from a string; a '.' after a char sets its dot."""
        digits = []
        for ch in text:
            if ch == "." and digits:
                digits[-1] = (digits[-1][0], True)
            else:
                digits.append((ch, False))
        digits = (digits + [(" ", False)] * 10)[:10]
        for i, (ch, dot) in enumerate(digits):
            self.send(p.cc(0x49 - i, p.seg7_encode(ch, dot)))

    def assign(self, text):
        text = (text + "  ")[:2]
        self.send(p.cc(0x4B, p.seg7_encode(text[0])))
        self.send(p.cc(0x4A, p.seg7_encode(text[1])))

    def meter(self, strip, level):
        self.send([0xD0, (strip << 4) | (level & 0x0F)])

    def clip(self, strip, on=True):
        self.send([0xD0, (strip << 4) | (0x0E if on else 0x0F)])

    # --- simulation ---------------------------------------------------------

    def _value_text(self, track):
        if track in self.touched:
            return format_db(fake_db(self.faders[track]))
        if self.mode == "pan":
            pan = self.pans[track]
            return "C" if pan == 0 else (f"L{-pan}" if pan < 0 else f"R{pan}")
        if self.mode == "track":
            return format_db(fake_db(self.faders[track]))
        return {"send": "-12.0", "plugin": "ChEQ", "eq": "1.0k", "instrument": "Sine"}[self.mode]

    def paint_value(self, strip):
        track = self.bank + strip
        self.lcd(p.LCD_LINE + strip * 7, f"{self._value_text(track):<6} "[:7])

    def paint_names(self):
        names = TRACKS[self.bank:self.bank + 8]
        self.lcd(0, "".join(f"{n[:6]:<6} " for n in names).ljust(p.LCD_LINE))

    def paint_values(self):
        cells = "".join(f"{self._value_text(self.bank + i):<6} "[:7] for i in range(8))
        self.lcd(p.LCD_LINE, cells.ljust(p.LCD_LINE))

    def paint_strip_leds(self):
        for i in range(8):
            t = self.bank + i
            self.led(0x00 + i, 0x7F if t in self.recs else 0)
            self.led(0x08 + i, 0x7F if t in self.solos else 0)
            self.led(0x10 + i, 0x7F if t in self.mutes else 0)
            self.led(0x18 + i, 0x7F if t == self.selected else 0)
            self.fader(i, self.faders[t])
            pan = self.pans[t]
            self.vpot_ring(i, 0x10 | max(1, min(11, 6 + round(pan / 12))))

    def paint_automation(self):
        mode = self.automation[self.selected]
        for name in p.AUTOMATION_BUTTONS[:5]:
            self.led(p.BUTTONS[name], 0x7F if name == mode else 0)

    def paint_transport(self):
        self.led(p.BUTTONS["play"], 0x7F if self.playing else 0)
        self.led(p.BUTTONS["stop"], 0 if self.playing else 0x7F)
        self.led(p.BUTTONS["record"], 0x7F if self.recording else 0)
        self.led(p.BUTTONS["smpte_led"], 0 if self.beats_mode else 0x7F)
        self.led(p.BUTTONS["beats_led"], 0x7F if self.beats_mode else 0)
        self.led(p.BUTTONS["rude_solo_led"], 0x7F if self.solos else 0)

    def paint_time(self):
        ticks = self.position
        if self.beats_mode:
            bar, rest = divmod(ticks, 16)
            beat, div = divmod(rest, 4)
            self.time_digits(f"{bar + 1:>3}.{beat + 1:>2}.{div + 1:>2}.  0")
        else:
            frames = ticks * 12
            h, rest = divmod(frames, 24 * 3600)
            mn, rest = divmod(rest, 24 * 60)
            sec, fr = divmod(rest, 24)
            self.time_digits(f" {h:>2}.{mn:02d}.{sec:02d}.{fr:02d}")

    def paint_all(self):
        self.paint_names()
        self.paint_values()
        self.paint_strip_leds()
        self.paint_automation()
        self.paint_transport()
        self.assign(MODE_ASSIGN[self.mode])
        self.paint_time()
        self.sysex([0x21, 0x01])
        for i in range(8):
            self.sysex([0x20, i, 0x07])

    def _fader_moved(self, strip, value14):
        if strip == p.MASTER:
            self.fader(p.MASTER, value14)
            return
        track = self.bank + strip
        self.faders[track] = value14
        self.fader(strip, value14)
        self.paint_value(strip)

    def _button(self, note, vel):
        pressed = vel > 0
        name = p.button_name(note)
        if 0x68 <= note <= 0x6F:
            strip = note - 0x68
            track = self.bank + strip
            if pressed:
                self.touched.add(track)
            else:
                self.touched.discard(track)
            self.paint_value(strip)
            return
        if not pressed:
            if name in ("rewind", "fast_fwd"):
                self.led(note, 0)
            return
        if note < 0x20:
            strip = note & 7
            track = self.bank + strip
            group = note >> 3
            if group == 0:
                self.recs ^= {track}
            elif group == 1:
                self.solos ^= {track}
            elif group == 2:
                self.mutes ^= {track}
            else:
                self.selected = track
                self.paint_automation()
            self.paint_strip_leds()
            self.paint_transport()
        elif 0x20 <= note <= 0x27:
            self.pans[self.bank + (note - 0x20)] = 0
            self.paint_value(note - 0x20)
            self.paint_strip_leds()
        elif name in MODE_ASSIGN:
            self.mode = name
            self.assign(MODE_ASSIGN[name])
            self.paint_values()
        elif name in ("bank_left", "bank_right", "channel_left", "channel_right"):
            step = 8 if name.startswith("bank") else 1
            step = -step if name.endswith("left") else step
            self.bank = max(0, min(len(TRACKS) - 8, self.bank + step))
            self.paint_names()
            self.paint_values()
            self.paint_strip_leds()
        elif name == "play":
            self.playing = True
            self.paint_transport()
            self._start_ticker()
        elif name == "stop":
            self.playing = False
            self.recording = False
            self.paint_transport()
        elif name == "record":
            self.recording = not self.recording
            self.paint_transport()
        elif name in ("rewind", "fast_fwd"):
            self.led(note, 0x7F)
            if name == "rewind":
                self.position = 0
                self.paint_time()
        elif name in p.AUTOMATION_BUTTONS[:5]:
            current = self.automation[self.selected]
            if name == "read_off":
                self.automation[self.selected] = "off" if current == "read_off" else "read_off"
            else:
                self.automation[self.selected] = name
            self.paint_automation()
        elif name == "smpte_beats":
            self.beats_mode = not self.beats_mode
            self.paint_transport()
            self.paint_time()
        elif name == "name_value":
            self.paint_values()
        elif name in ("shift", "option", "control", "alt"):
            pass
        else:
            self.led(note, 0 if self.leds.get(note) else 0x7F)

    def _start_ticker(self):
        if self._ticker is not None and self._ticker.is_alive():
            return
        self._ticker = threading.Thread(target=self._tick, name="fake-logic-ticker", daemon=True)
        self._ticker.start()

    def _tick(self):
        t = 0
        while not self._stop.is_set() and self.playing:
            t += 1
            for i in range(8):
                level = int(6.5 + 5.5 * math.sin(t / 3 + i))
                self.meter(i, max(0, min(12, level)))
                if level >= 12 and t % 7 == 0:
                    self.clip(i, True)
            if t % 5 == 0:
                self.position += 1
                self.paint_time()
            time.sleep(0.1)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port-name", default="Claude MCU test",
                        help='daemon port to attach to (default "Claude MCU test")')
    parser.add_argument("--device-id", default="0x7E", help="Mackie device id the daemon was started with (default 0x7E)")
    parser.add_argument("--seconds", type=float, default=0, help="quit after this long (default: until Ctrl-C)")
    parser.add_argument("--no-simulate", action="store_true", help="handshake only; do not paint or react")
    parser.add_argument("--quiet", action="store_true", help="do not print received messages")
    args = parser.parse_args()

    text = args.device_id.strip().lower()
    device_id = int(text, 16) if text.startswith("0x") else int(text)
    if device_id == p.DEVICE_ID and args.port_name == "Claude MCU":
        sys.exit("refusing to fake-host the real 'Claude MCU' port with device id 0x14 — "
                 "Logic Pro may be connected to it. Use --port-name 'Claude MCU test' --device-id 0x7E.")

    def log(line):
        if not args.quiet:
            print(time.strftime("%H:%M:%S"), line, flush=True)

    host = FakeHost(args.port_name, device_id, simulate=not args.no_simulate, log=log)
    try:
        host.open()
    except PortError as exc:
        sys.exit(f"{exc}\nStart the daemon first: mcuctl daemon start --port-name '{args.port_name}' --device-id 0x{device_id:02X}")
    log(f"attached to {args.port_name!r} (device id 0x{device_id:02X}); sending device query")
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    host.device_query()
    host.version_request()
    deadline = time.monotonic() + args.seconds if args.seconds else None
    while not stop.is_set() and (deadline is None or time.monotonic() < deadline):
        time.sleep(0.1)
    host.close()
    log("fake host closed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
