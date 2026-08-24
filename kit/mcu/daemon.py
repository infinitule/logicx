#!/usr/bin/env python3
"""The Claude MCU daemon: owns the virtual CoreMIDI ports, keeps State, serves a socket.

    python3 daemon.py start  [--run-dir DIR] [--port-name NAME] [--device-id 0x14]
    python3 daemon.py run    [...]            # same, in the foreground (what start spawns)
    python3 daemon.py stop   [--run-dir DIR]
    python3 daemon.py status [--run-dir DIR] [--json]

Normally you do not run this directly; `mcuctl daemon start|stop|status|log` wraps it.

Files in the run dir (default kit/mcu/run; override with --run-dir or $MCU_RUN_DIR):
    daemon.pid    daemon.log    mcu.sock    daemon.json (pid, port, device id, start time)

Socket protocol: unix stream socket, one JSON object per line each way.
    {"cmd": "state"}                      -> {"ok": true, "state": {...}, "seq": N, "daemon": {...}}
    {"cmd": "events", "since": N, "max": 200}
    {"cmd": "wait", "for": "lcd|fader|led|meter|vpot|time|assign|sysex|handshake|any",
                    "timeout_ms": 1000, "since": N, "strip": 0-8, "note": nn, "line": "top|bottom"}
    {"cmd": "send", "bytes": [..]}   note {n, vel}   cc {n, v}   pb {strip, value14}   sysex {bytes}
    {"cmd": "press", "button": "play", "hold_ms": 40, "modifiers": ["shift"]}
    touch {strip, on}   fader {strip, value14, touch}   vpot {strip, delta}   vpush {strip}   jog {delta}
    handshake   reset_meters   ping   quit
Replies are {"ok": true, ...} or {"ok": false, "error": "..."}. Strips are 0-7, 8 or "master".

Use --device-id to emulate a different Mackie device id. Tests use 0x7E so that a
running Logic Pro, which probes every new MIDI port for ids 0x10/0x11/0x14/0x15/0x17,
ignores the test daemon instead of auto-installing it as a control surface.
"""

import argparse
import hashlib
import json
import logging
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).absolute().parent.parent))
    __package__ = "mcu"

from . import protocol as p  # noqa: E402
from .client import Client, DaemonError, DaemonNotRunning, socket_path  # noqa: E402
from .state import State  # noqa: E402

DEFAULT_PORT_NAME = "Claude MCU"
# Not resolve(): the skill dir may be a symlink into a much deeper tree, and the
# socket path has to stay short (see choose_socket_path).
DEFAULT_RUN_DIR = Path(__file__).absolute().parent / "run"
RING_SIZE = 2000
MAX_WAIT_MS = 60_000
DEFAULT_HOLD_MS = 40
MAX_SOCKET_PATH = 96  # macOS sun_path is 104 bytes; keep a margin

log = logging.getLogger("mcu.daemon")


class PortError(Exception):
    pass


def run_dir_from(arg=None):
    return Path(arg or os.environ.get("MCU_RUN_DIR") or DEFAULT_RUN_DIR).expanduser()


def parse_device_id(text):
    """'0x14' or '20' -> 20. Must fit in 7 bits."""
    if isinstance(text, int):
        value = text
    else:
        text = str(text).strip().lower()
        value = int(text, 16) if text.startswith("0x") else int(text)
    if not 0 <= value <= 0x7F:
        raise ValueError(f"device id {text!r} must be 0x00-0x7F")
    return value


def choose_socket_path(run_dir):
    """<run_dir>/mcu.sock, or a short path in the temp dir when that would not fit AF_UNIX."""
    direct = Path(run_dir) / "mcu.sock"
    if len(str(direct).encode("utf-8")) <= MAX_SOCKET_PATH:
        return direct
    digest = hashlib.sha1(str(Path(run_dir).absolute()).encode("utf-8")).hexdigest()[:10]
    return Path(tempfile.gettempdir()) / f"claude-mcu-{digest}.sock"


def validate_midi(message):
    """Refuse anything CoreMIDI would re-parse into a different message.

    An 8-bit data byte becomes a new status byte on the wire (0x90 0xDF 0x7F reaches
    the host as 0x90 0x5F 0x7F = RECORD, bypassing the CLI's --yes guard), and a
    sysex without its F7 swallows every later message into itself.
    """
    if not message:
        raise ValueError("empty MIDI message")
    if message[0] == 0xF0:
        if len(message) < 2 or message[-1] != 0xF7:
            raise ValueError("sysex must end with F7 (an unterminated sysex swallows every later message)")
        for b in message[1:-1]:
            if b >= 0x80:
                raise ValueError(f"sysex data byte 0x{b:02X} is not 7-bit")
        return
    status_byte = message[0]
    if not 0x80 <= status_byte <= 0xEF:
        raise ValueError(f"0x{status_byte:02X} is not a channel-message status byte")
    need = 2 if (status_byte & 0xF0) in (0xC0, 0xD0) else 3
    if len(message) != need:
        raise ValueError(f"status 0x{status_byte:02X} needs {need} bytes, got {len(message)}")
    for b in message[1:]:
        if b >= 0x80:
            raise ValueError(f"data byte 0x{b:02X} is not 7-bit (would be re-parsed as a status byte)")


def pid_is_daemon(pid):
    """True only if `pid` is one of our daemon processes (guards pid reuse / corrupt pidfiles)."""
    try:
        out = subprocess.run(["ps", "-o", "command=", "-p", str(pid)],
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "daemon.py" in out and " run" in out


def pid_alive(pid):
    if not isinstance(pid, int) or pid <= 1:
        return False
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, TypeError):
        return False
    except PermissionError:
        return True
    return True


class Daemon:
    WAIT_KINDS = {
        "lcd": {"lcd"},
        "fader": {"fader"},
        "led": {"led"},
        "meter": {"meter"},
        "vpot": {"vpot_ring"},
        "time": {"time_digit", "sysex_time_text"},
        "assign": {"assign_digit", "sysex_assign_text"},
        "handshake": {"sysex_device_query", "sysex_host_reply"},
    }

    def __init__(self, run_dir, port_name=DEFAULT_PORT_NAME, device_id=p.DEVICE_ID,
                 serial=p.SERIAL, challenge=p.CHALLENGE, hold_ms=DEFAULT_HOLD_MS):
        self.run_dir = Path(run_dir)
        self.port_name = port_name
        self.device_id = device_id
        self.serial = serial
        self.challenge = list(challenge)
        self.hold_ms = hold_ms
        self.state = State()
        self.cond = threading.Condition()
        self.events = deque(maxlen=RING_SIZE)
        self.seq = 0
        self.out_lock = threading.Lock()
        # A gesture (modifier down, button down, hold, up, modifier up; touch, move,
        # release) must not interleave with another client's gesture.
        self.gesture_lock = threading.RLock()
        self.stop_event = threading.Event()
        self.started = time.time()
        self.midi_in = None
        self.midi_out = None
        self.server = None
        self.serve_error = None
        self.sock_path = choose_socket_path(self.run_dir)

    # --- MIDI ---------------------------------------------------------------

    def open_ports(self):
        try:
            import rtmidi
        except ImportError as exc:
            raise PortError(f"python-rtmidi is not installed in this interpreter ({sys.executable}): {exc}")
        existing = set(rtmidi.MidiIn().get_ports()) | set(rtmidi.MidiOut().get_ports())
        if any(name == self.port_name or name.startswith(self.port_name + " ") for name in existing):
            raise PortError(
                f"a MIDI port named {self.port_name!r} already exists (another daemon, or an orphan). "
                "Stop it first (mcuctl daemon stop / pkill -f mcu/daemon.py) or pick --port-name.")
        try:
            self.midi_out = rtmidi.MidiOut(name=self.port_name)
            self.midi_out.open_virtual_port(self.port_name)
            self.midi_in = rtmidi.MidiIn(name=self.port_name)
            self.midi_in.ignore_types(sysex=False, timing=True, active_sense=True)
            self.midi_in.open_virtual_port(self.port_name)
            self.midi_in.set_callback(self._on_midi)
        except Exception as exc:  # rtmidi raises its own SystemError subclass
            self.close_ports()
            raise PortError(
                f"could not open virtual MIDI ports named {self.port_name!r}: {exc}\n"
                "CoreMIDI virtual ports need a working MIDI server; check Audio MIDI Setup "
                "and that no sandbox blocks it."
            ) from exc
        log.info("virtual ports open: %r (device id 0x%02X)", self.port_name, self.device_id)

    def close_ports(self):
        for port in (self.midi_in, self.midi_out):
            if port is not None:
                try:
                    port.close_port()
                except Exception:  # noqa: BLE001
                    pass
        self.midi_in = self.midi_out = None

    def _record(self, event, direction, ts=None):
        """Append to the ring. Caller holds self.cond."""
        self.seq += 1
        event["seq"] = self.seq
        event["ts"] = ts if ts is not None else time.time()
        event["dir"] = direction
        self.events.append(event)
        self.cond.notify_all()

    def _on_midi(self, event, data=None):
        try:
            message, _delta = event
            ts = time.time()
            decoded = p.decode(message, self.device_id)
            with self.cond:
                self.state.apply(decoded, ts)
                self._record(decoded, "in", ts)
            if decoded["kind"] in ("sysex_device_query", "sysex_host_reply", "sysex_version_request",
                                   "sysex_offline", "sysex_unknown", "unknown"):
                log.info("host: %s", p.describe_event(decoded))
            else:
                log.debug("host: %s", p.describe_event(decoded))
            self._react(decoded, ts)
        except Exception:  # noqa: BLE001 - the callback thread must survive anything
            log.exception("error handling MIDI input %r", event)

    def _react(self, decoded, ts):
        kind = decoded["kind"]
        if kind == "sysex_device_query":
            self.send(p.host_connection_query(self.serial, self.challenge, self.device_id))
            with self.cond:
                self.state.handshake_ts["query_sent"] = time.time()
        elif kind == "sysex_host_reply":
            self.send(p.host_connection_confirm(self.serial, self.device_id))
            with self.cond:
                self.state.set_handshake("confirmed", time.time())
                self.cond.notify_all()
            log.info("handshake confirmed (host serial %r)", decoded.get("serial"))
        elif kind == "sysex_version_request":
            self.send(p.version_reply(device_id=self.device_id))

    def send(self, message, desc=None):
        message = [int(b) for b in message]
        if any(not 0 <= b <= 0xFF for b in message):
            raise ValueError(f"bad MIDI bytes: {message}")
        validate_midi(message)
        if self.midi_out is None:
            raise DaemonError("MIDI output port is not open")
        with self.out_lock:
            self.midi_out.send_message(message)
        desc = desc or p.describe_outgoing(message)
        with self.cond:
            self._record({"kind": "sent", "bytes": message, "desc": desc}, "out")
        log.debug("sent: %s", desc)
        return message

    # --- socket server --------------------------------------------------------

    def serve(self):
        try:
            if self.sock_path.exists():
                self.sock_path.unlink()
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server.bind(str(self.sock_path))
            server.listen(16)
            server.settimeout(0.5)
        except OSError as exc:
            self.serve_error = f"could not listen on {self.sock_path}: {exc}"
            log.error("%s", self.serve_error)
            self.stop_event.set()
            return
        self.server = server
        log.info("serving %s (pid %d)", self.sock_path, os.getpid())
        while not self.stop_event.is_set():
            try:
                conn, _ = server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn):
        with conn:
            conn.settimeout(30)
            buf = b""
            while not self.stop_event.is_set():
                try:
                    chunk = conn.recv(1 << 16)
                except (socket.timeout, OSError):
                    break
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if not line.strip():
                        continue
                    reply = self.dispatch(line)
                    try:
                        conn.sendall(json.dumps(reply).encode("utf-8") + b"\n")
                    except OSError:
                        return
                    if reply.get("quitting"):
                        return

    def dispatch(self, line):
        try:
            request = json.loads(line)
        except ValueError as exc:
            return {"ok": False, "error": f"bad JSON: {exc}"}
        if not isinstance(request, dict) or "cmd" not in request:
            return {"ok": False, "error": 'request must be a JSON object with a "cmd" key'}
        cmd = request.pop("cmd")
        handler = getattr(self, f"cmd_{cmd}", None) if isinstance(cmd, str) else None
        if handler is None:
            names = sorted(n[4:] for n in dir(self) if n.startswith("cmd_"))
            return {"ok": False, "error": f"unknown command {cmd!r}; commands: {', '.join(names)}"}
        try:
            result = handler(**request)
        except TypeError as exc:
            return {"ok": False, "error": f"{cmd}: bad arguments: {exc}"}
        except (ValueError, KeyError, IndexError, DaemonError) as exc:
            return {"ok": False, "error": f"{cmd}: {exc}"}
        except Exception as exc:  # noqa: BLE001
            log.exception("command %s failed", cmd)
            return {"ok": False, "error": f"{cmd}: internal error: {exc}"}
        return {"ok": True, **result}

    # --- commands -------------------------------------------------------------

    def info(self):
        now = time.time()
        with self.cond:
            last = self.state.last_host_ts
            handshake = self.state.handshake
            online = self.state.online
            seq = self.seq
        return {
            "pid": os.getpid(),
            "port_name": self.port_name,
            "device_id": f"0x{self.device_id:02X}",
            "started": self.started,
            "uptime_s": round(now - self.started, 1),
            "ports_open": self.midi_in is not None and self.midi_out is not None,
            "run_dir": str(self.run_dir),
            "seq": seq,
            "handshake": handshake,
            "online": online,
            "last_host_ts": last,
            "last_host_age_s": round(now - last, 2) if last else None,
        }

    def _strip(self, strip, allow_master=True):
        if isinstance(strip, str):
            if strip.strip().lower() in ("master", "m"):
                strip = p.MASTER
            else:
                strip = int(strip)
        strip = int(strip)
        top = p.MASTER if allow_master else 7
        if not 0 <= strip <= top:
            raise ValueError(f"strip {strip} out of range 0-{top}" + (" (8 = master)" if allow_master else ""))
        return strip

    def cmd_ping(self):
        return {"pong": True, "pid": os.getpid()}

    def cmd_state(self):
        with self.cond:
            snapshot = self.state.snapshot()
            seq = self.seq
        return {"state": snapshot, "seq": seq, "daemon": self.info()}

    def cmd_events(self, since=None, max=200):
        max = int(max)
        with self.cond:
            picked = [e for e in self.events if since is None or e["seq"] > int(since)]
            more = len(picked) > max
            result = {"events": picked[:max], "seq": self.seq, "more": more}
            result.update(self._eviction(since))
            return result

    def _eviction(self, since):
        """How many events after `since` already fell off the ring (callers must resync from state)."""
        if since is None:
            return {"evicted": 0}
        oldest = self.events[0]["seq"] if self.events else self.seq + 1
        evicted = max(0, oldest - int(since) - 1)
        return {"evicted": evicted, "first_seq": oldest} if evicted else {"evicted": 0}

    def _matches(self, event, what, strip, note, line):
        if event.get("dir") != "in":
            return False
        kind = event["kind"]
        if what == "any":
            ok = True
        elif what == "sysex":
            ok = kind.startswith("sysex_")
        elif what in self.WAIT_KINDS:
            ok = kind in self.WAIT_KINDS[what]
        else:
            ok = kind == what
        if not ok:
            return False
        if strip is not None:
            if kind == "lcd":
                lo, hi = event["offset"], event["offset"] + len(event["text"])
                cells = [(strip * p.STRIP_WIDTH, (strip + 1) * p.STRIP_WIDTH),
                         (p.LCD_LINE + strip * p.STRIP_WIDTH, p.LCD_LINE + (strip + 1) * p.STRIP_WIDTH)]
                if not any(lo < c_hi and hi > c_lo for c_lo, c_hi in cells):
                    return False
            elif event.get("strip") != strip:
                return False
        if note is not None and event.get("note") != note:
            return False
        if line is not None and kind == "lcd" and (event["offset"] < p.LCD_LINE) != (line == "top"):
            return False
        return True

    def cmd_wait(self, **args):
        what = args.pop("for", "any")
        timeout_ms = min(int(args.pop("timeout_ms", 1000)), MAX_WAIT_MS)
        since = args.pop("since", None)
        strip = args.pop("strip", None)
        note = args.pop("note", None)
        line = args.pop("line", None)
        if args:
            raise ValueError(f"unknown wait arguments: {', '.join(args)}")
        if what not in ("any", "sysex") and what not in self.WAIT_KINDS and what not in (
                "time_digit", "assign_digit", "vpot_ring", "sysex_device_query", "sysex_host_reply",
                "sysex_version_request", "sysex_offline", "sysex_misc", "sysex_unknown", "unknown"):
            raise ValueError(f"unknown wait target {what!r}; use lcd, fader, led, meter, vpot, time, "
                             "assign, sysex, handshake or any")
        if strip is not None:
            strip = self._strip(strip)
        if note is not None:
            note = p.button_note(note)
        deadline = time.monotonic() + timeout_ms / 1000
        with self.cond:
            if since is None:
                since = self.seq
            since = int(since)
            while True:
                hits = [e for e in self.events if e["seq"] > since and self._matches(e, what, strip, note, line)]
                if hits:
                    return {"events": hits, "timed_out": False, "seq": self.seq, **self._eviction(since)}
                remaining = deadline - time.monotonic()
                if remaining <= 0 or self.stop_event.is_set():
                    return {"events": [], "timed_out": True, "seq": self.seq, **self._eviction(since)}
                self.cond.wait(remaining)

    def cmd_send(self, bytes):  # noqa: A002 - the wire name is "bytes"
        return {"sent": [self.send(bytes)]}

    def cmd_note(self, n, vel=0x7F):
        return {"sent": [self.send(p.note_on(p.button_note(n), vel))]}

    def cmd_cc(self, n, v):
        return {"sent": [self.send(p.cc(n, v))]}

    def cmd_pb(self, strip, value14):
        return {"sent": [self.send(p.pitch_bend(self._strip(strip), value14))]}

    def cmd_sysex(self, bytes):  # noqa: A002
        data = [int(b) for b in bytes]
        if data and data[0] == 0xF0:
            message = data
        else:
            message = p.sysex(data, self.device_id)
        return {"sent": [self.send(message)]}

    def cmd_press(self, button, hold_ms=None, modifiers=None):
        note = p.button_note(button)
        mods = [p.button_note(m) for m in (modifiers or [])]
        hold = self.hold_ms if hold_ms is None else max(0, int(hold_ms))
        sent = []
        with self.gesture_lock:
            for m in mods:
                sent.append(self.send(p.note_on(m)))
            sent.append(self.send(p.note_on(note)))
            time.sleep(hold / 1000)
            sent.append(self.send(p.note_off(note)))
            for m in reversed(mods):
                sent.append(self.send(p.note_off(m)))
        return {"note": note, "name": p.button_name(note), "hold_ms": hold, "sent": sent}

    def cmd_touch(self, strip, on=True):
        strip = self._strip(strip)
        on = bool(on) if not isinstance(on, str) else on.lower() in ("on", "1", "true", "yes")
        with self.gesture_lock:
            sent = self.send(p.fader_touch(strip, on))
            with self.cond:
                self.state.set_touched(strip, on)
        return {"strip": strip, "on": on, "sent": [sent]}

    def cmd_fader(self, strip, value14, touch=True, settle_ms=None):
        strip = self._strip(strip)
        value14 = max(0, min(16383, int(value14)))
        sent = []
        with self.gesture_lock:
            if touch:
                sent.append(self.cmd_touch(strip, True)["sent"][0])
            sent.append(self.send(p.pitch_bend(strip, value14)))
            if touch:
                time.sleep((self.hold_ms if settle_ms is None else int(settle_ms)) / 1000)
                sent.append(self.cmd_touch(strip, False)["sent"][0])
        return {"strip": strip, "value14": value14, "touch": bool(touch), "sent": sent}

    def cmd_vpot(self, strip, delta):
        strip = self._strip(strip, allow_master=False)
        return {"strip": strip, "delta": int(delta),
                "sent": [self.send(m) for m in p.vpot_turn(strip, delta)]}

    def cmd_vpush(self, strip, hold_ms=None):
        strip = self._strip(strip, allow_master=False)
        hold = self.hold_ms if hold_ms is None else max(0, int(hold_ms))
        with self.gesture_lock:
            sent = [self.send(p.vpot_push(strip))]
            time.sleep(hold / 1000)
            sent.append(self.send(p.note_off(0x20 + strip)))
        return {"strip": strip, "hold_ms": hold, "sent": sent}

    def cmd_jog(self, delta):
        return {"delta": int(delta), "sent": [self.send(m) for m in p.jog(delta)]}

    def cmd_handshake(self):
        """Send an unsolicited Host Connection Query (handshake step 2)."""
        sent = self.send(p.host_connection_query(self.serial, self.challenge, self.device_id))
        with self.cond:
            self.state.handshake_ts["query_sent"] = time.time()
        return {"sent": [sent]}

    def cmd_reset_meters(self):
        with self.cond:
            self.state.reset_meters()
        return {"reset": True}

    def cmd_quit(self):
        self.stop_event.set()
        return {"quitting": True}

    # --- lifecycle ------------------------------------------------------------

    def run(self):
        """Open ports, serve until told to stop, tidy up. Returns an exit code."""
        self.run_dir.mkdir(parents=True, exist_ok=True)
        current = status(self.run_dir)
        if current["running"] and current.get("pid") != os.getpid():
            msg = f"daemon already running on this run dir (pid {current['pid']}); refusing to start a second one"
            log.error("%s", msg)
            print(f"mcu daemon: {msg}", file=sys.stderr)
            return 1
        try:
            self.open_ports()
        except PortError as exc:
            log.error("%s", exc)
            print(f"mcu daemon: {exc}", file=sys.stderr)
            return 1
        (self.run_dir / "daemon.pid").write_text(f"{os.getpid()}\n")
        (self.run_dir / "daemon.json").write_text(json.dumps({
            "pid": os.getpid(), "port_name": self.port_name, "device_id": f"0x{self.device_id:02X}",
            "started": self.started, "python": sys.executable, "socket": str(self.sock_path),
        }, indent=2))
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(sig, lambda *_: self.stop_event.set())
        server_thread = threading.Thread(target=self.serve, name="mcu-socket", daemon=True)
        server_thread.start()
        while self.server is None and self.serve_error is None and not self.stop_event.is_set():
            time.sleep(0.01)
        if self.serve_error:
            print(f"mcu daemon: {self.serve_error}", file=sys.stderr)
        try:
            self.cmd_handshake()
        except Exception as exc:  # noqa: BLE001
            log.warning("could not send the initial host connection query: %s", exc)
        while not self.stop_event.wait(0.25):
            pass
        log.info("stopping")
        if self.server is not None:
            try:
                self.server.close()
            except OSError:
                pass
        self.close_ports()
        for path in (self.sock_path, self.run_dir / "daemon.pid", self.run_dir / "daemon.json"):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        return 1 if self.serve_error else 0


# --- process management (used by the CLI) ---------------------------------------

def setup_logging(run_dir, verbose=False, stderr=False):
    run_dir.mkdir(parents=True, exist_ok=True)
    handlers = [logging.FileHandler(run_dir / "daemon.log")]
    if stderr:
        handlers.append(logging.StreamHandler(sys.stderr))
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
        force=True,
    )


def read_pid(run_dir):
    try:
        pid = int((Path(run_dir) / "daemon.pid").read_text().strip())
    except (FileNotFoundError, ValueError):
        return None
    return pid if pid > 1 else None


def read_info(run_dir):
    try:
        return json.loads((Path(run_dir) / "daemon.json").read_text())
    except (FileNotFoundError, ValueError):
        return {}


def clean_stale(run_dir):
    run_dir = Path(run_dir)
    for path in (socket_path(run_dir), run_dir / "mcu.sock", run_dir / "daemon.pid", run_dir / "daemon.json"):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def log_tail(run_dir, lines=40):
    try:
        text = (Path(run_dir) / "daemon.log").read_text(errors="replace")
    except FileNotFoundError:
        return ""
    return "\n".join(text.splitlines()[-lines:])


def status(run_dir):
    """What the pidfile and socket say. Never raises."""
    run_dir = Path(run_dir)
    pid = read_pid(run_dir)
    alive = pid is not None and pid_alive(pid) and pid_is_daemon(pid)
    result = {"running": alive, "pid": pid, "run_dir": str(run_dir), "socket": str(socket_path(run_dir))}
    result.update({k: v for k, v in read_info(run_dir).items() if k != "pid"})
    if alive:
        try:
            result.update(Client(run_dir).call("state", timeout=3.0)["daemon"])
            result["responding"] = True
        except (DaemonNotRunning, DaemonError) as exc:
            result["responding"] = False
            result["error"] = str(exc)
    return result


def start_background(run_dir, port_name=DEFAULT_PORT_NAME, device_id=p.DEVICE_ID, python=None,
                     wait_s=8.0, verbose=False):
    """Spawn `daemon.py run` detached, wait until its socket answers. Returns status()."""
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    current = status(run_dir)
    if current["running"]:
        raise DaemonError(
            f"daemon already running (pid {current['pid']}, port {current.get('port_name')!r}). "
            f"Stop it first: mcuctl daemon stop")
    clean_stale(run_dir)
    cmd = [python or sys.executable, str(Path(__file__).resolve()), "run",
           "--run-dir", str(run_dir), "--port-name", port_name, "--device-id", f"0x{device_id:02X}"]
    if verbose:
        cmd.append("--verbose")
    with open(run_dir / "daemon.log", "ab") as logf:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=logf, stderr=logf,
                                start_new_session=True)
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        code = proc.poll()
        if code is not None:
            raise DaemonError(
                f"daemon exited with code {code} before serving. Log tail:\n{log_tail(run_dir, 15)}")
        if socket_path(run_dir).exists():
            try:
                Client(run_dir).ping()
                return status(run_dir)
            except (DaemonNotRunning, DaemonError):
                pass
        time.sleep(0.1)
    # Never leave a half-started daemon behind.
    proc.terminate()
    try:
        proc.wait(3)
    except subprocess.TimeoutExpired:
        proc.kill()
    clean_stale(run_dir)
    raise DaemonError(f"daemon (pid {proc.pid}) did not answer within {wait_s}s and was killed. "
                      f"Log tail:\n{log_tail(run_dir, 15)}")


def stop(run_dir, timeout=5.0):
    """Ask the daemon to quit, escalate to SIGTERM then SIGKILL. Returns True if one was running."""
    run_dir = Path(run_dir)
    pid = read_pid(run_dir)
    alive = pid is not None and pid_alive(pid) and pid_is_daemon(pid)
    if not alive:
        # A dead, reused or corrupt pidfile is never a reason to signal anything.
        clean_stale(run_dir)
        return False
    try:
        Client(run_dir).call("quit", timeout=2.0)
    except (DaemonNotRunning, DaemonError):
        pass
    for sig, wait in ((None, timeout / 2), (signal.SIGTERM, timeout / 2), (signal.SIGKILL, 1.0)):
        if sig is not None and pid_alive(pid):
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + wait
        while pid_alive(pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        if not pid_alive(pid):
            break
    clean_stale(run_dir)
    return True


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["run", "start", "stop", "status"])
    parser.add_argument("--run-dir", help="pidfile/socket/log directory (default kit/mcu/run or $MCU_RUN_DIR)")
    parser.add_argument("--port-name", default=DEFAULT_PORT_NAME, help='virtual MIDI port name (default "Claude MCU")')
    parser.add_argument("--device-id", default=f"0x{p.DEVICE_ID:02X}", help="Mackie device id (default 0x14)")
    parser.add_argument("--verbose", action="store_true", help="log every MIDI message")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    run_dir = run_dir_from(args.run_dir)
    try:
        device_id = parse_device_id(args.device_id)
    except ValueError as exc:
        sys.exit(str(exc))

    if args.action == "run":
        setup_logging(run_dir, verbose=args.verbose, stderr=sys.stderr.isatty())
        return Daemon(run_dir, args.port_name, device_id).run()

    if args.action == "start":
        try:
            info = start_background(run_dir, args.port_name, device_id, verbose=args.verbose)
        except DaemonError as exc:
            sys.exit(f"could not start the daemon: {exc}")
        print(json.dumps(info, indent=2) if args.json else
              f"daemon started: pid {info['pid']}, port {info.get('port_name')!r}, socket {info['socket']}")
        return 0

    if args.action == "stop":
        was = stop(run_dir)
        print(json.dumps({"stopped": was}) if args.json else ("daemon stopped" if was else "daemon was not running"))
        return 0

    info = status(run_dir)
    if args.json:
        print(json.dumps(info, indent=2))
    elif info["running"]:
        print(f"daemon running: pid {info['pid']}, port {info.get('port_name')!r}, up {info.get('uptime_s', '?')}s, "
              f"handshake {info.get('handshake', '?')}, last host message "
              f"{info.get('last_host_age_s', 'never') if info.get('last_host_age_s') is not None else 'never'}s ago")
    else:
        print(f"daemon not running (run dir {run_dir})")
    return 0 if info["running"] else 2


if __name__ == "__main__":
    sys.exit(main())
