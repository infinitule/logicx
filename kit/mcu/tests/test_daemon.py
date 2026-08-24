"""Integration: a real daemon subprocess, real CoreMIDI virtual ports, fake host opposite.

The daemon is started on "Claude MCU test" with device id 0x7E so that a Logic
Pro that happens to be running ignores it (Logic probes new ports for the real
Mackie ids and would otherwise auto-install the test surface). Skips cleanly if
virtual ports cannot be created at all.
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

from mcu import daemon as daemon_mod
from mcu import protocol as p
from mcu.client import Client, DaemonError
from mcu.fake_logic import FakeHost, PortError

PORT = f"Claude MCU test {os.getpid()}"   # pid-unique: never the name Logic adopted
DEVICE_ID = 0x7E
HDR = [0xF0, 0x00, 0x00, 0x66, DEVICE_ID]
KIT = Path(__file__).resolve().parent.parent.parent
MCU_PY = KIT / "mcu" / "mcu.py"
LAUNCHER = KIT / "mcuctl"


@pytest.fixture(scope="module")
def run_dir():
    # mkdtemp, not tmp_path_factory: pytest's nested tmp dirs push the socket path
    # past macOS's 104-byte AF_UNIX limit (the daemon copes, but logs are tidier here).
    path = Path(tempfile.mkdtemp(prefix="mcu-test-"))
    try:
        daemon_mod.start_background(path, PORT, DEVICE_ID, python=sys.executable, verbose=True)
    except DaemonError as exc:
        shutil.rmtree(path, ignore_errors=True)
        if "could not open virtual MIDI ports" in str(exc) or "rtmidi" in str(exc):
            pytest.skip(f"CoreMIDI virtual ports unavailable here: {exc}")
        raise
    try:
        yield path
    finally:
        daemon_mod.stop(path)
        assert not daemon_mod.status(path)["running"], "daemon still running after stop()"
        shutil.rmtree(path, ignore_errors=True)


def test_socket_path_fallback_for_deep_run_dirs(tmp_path):
    short = Path("/tmp/mcu-x")
    assert daemon_mod.choose_socket_path(short) == short / "mcu.sock"
    deep = tmp_path / ("x" * 80) / "run"
    chosen = daemon_mod.choose_socket_path(deep)
    assert chosen.parent == Path(tempfile.gettempdir()) and chosen.name.startswith("claude-mcu-")
    assert len(str(chosen).encode()) <= daemon_mod.MAX_SOCKET_PATH


@pytest.fixture(scope="module")
def host(run_dir):
    fake = FakeHost(PORT, DEVICE_ID)
    try:
        fake.open(timeout=5.0)
    except PortError as exc:
        pytest.skip(f"could not attach to the daemon's ports from the host side: {exc}")
    try:
        yield fake
    finally:
        fake.close()


@pytest.fixture
def client(run_dir):
    return Client(run_dir)


def wait_state(client, predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while True:
        state = client.state()["state"]
        if predicate(state) or time.monotonic() > deadline:
            return state
        time.sleep(0.05)


def cli(run_dir, *argv, timeout=20):
    return subprocess.run([sys.executable, str(MCU_PY), "--run-dir", str(run_dir), *argv],
                          capture_output=True, text=True, timeout=timeout)


# --- handshake ----------------------------------------------------------------

def test_handshake_completes(host, client):
    host.device_query()
    query = host.wait_for(lambda m: m[:6] == HDR + [0x01], timeout=3.0)
    assert query is not None, "no Host Connection Query from the daemon"
    assert bytes(query[6:13]) == b"CLAUDE1"
    assert len(query) == 18 and query[-1] == 0xF7
    confirm = host.wait_for(lambda m: m[:6] == HDR + [0x03], timeout=3.0)
    assert confirm is not None, "no Host Connection Confirmation"
    assert bytes(confirm[6:13]) == b"CLAUDE1"
    state = wait_state(client, lambda s: s["handshake"] == "confirmed")
    assert state["handshake"] == "confirmed"
    assert state["handshake_complete"] is True
    assert state["online"] is True
    assert {"queried", "replied", "confirmed"} <= set(state["handshake_ts"])
    assert host.connected


def test_version_request_answered(host):
    start = host.count
    host.version_request()
    reply = host.wait_for(lambda m: m[:6] == HDR + [0x14], timeout=3.0, skip=start)
    assert reply is not None
    assert bytes(reply[6:-1]) == b"V1.0"


def test_real_mackie_id_is_ignored(host, client):
    """A 0x14 query must not be answered: on this machine that could be the real Logic."""
    start = host.count
    host.send([0xF0, 0x00, 0x00, 0x66, 0x14, 0x00, 0xF7])
    time.sleep(0.3)
    assert not any(m[:5] == [0xF0, 0x00, 0x00, 0x66, 0x14] for m in host.messages_since(start))
    events = client.events(since=0, max=2000)["events"]
    assert any(e["kind"] == "sysex_unknown" and e.get("device_id") == 0x14 for e in events)


# --- host -> state -------------------------------------------------------------

def test_lcd_write_shows_in_state(host, client):
    host.lcd(0, "Kick   Snare  HiHat  Bass   Gtr L  Gtr R  Keys   Vox    ")
    host.lcd(56, "-6.0   0.0    -inf   +2.5   C      L12    R30    -12.0  ")
    state = wait_state(client, lambda s: s["strips"][7]["value"] == "-12.0")
    assert state["lcd_top"].startswith("Kick   Snare  HiHat")
    names = [s["name"] for s in state["strips"]]
    assert names == ["Kick", "Snare", "HiHat", "Bass", "Gtr L", "Gtr R", "Keys", "Vox"]
    values = [s["value"] for s in state["strips"]]
    assert values == ["-6.0", "0.0", "-inf", "+2.5", "C", "L12", "R30", "-12.0"]
    # partial writes land in the right cells, including across the line boundary
    host.lcd(7, "Snr   ")
    host.lcd(54, "XY" + "AB")
    state = wait_state(client, lambda s: s["lcd_bottom"].startswith("AB"))
    assert state["strips"][1]["name"] == "Snr"
    assert state["lcd_top"].endswith("XY")
    assert state["strips"][0]["value"] == "AB.0" or state["lcd_bottom"].startswith("AB")


def test_led_echo_and_wait_by_note(host, client):
    seq = client.state()["seq"]
    host.led(p.BUTTONS["play"], 0x7F)
    got = client.wait("led", timeout_ms=3000, since=seq, note="play")
    assert not got["timed_out"]
    assert got["events"][0]["note"] == 0x5E and got["events"][0]["state"] == 0x7F
    host.led(0x12, 0x7F)       # mute strip 3
    host.led(0x1B, 0x01)       # select strip 4 blinking
    state = wait_state(client, lambda s: s["strips"][3]["select"] == 1)
    assert state["leds"]["play"] == 127
    assert state["strips"][2]["mute"] == 127
    assert state["strips"][3]["select"] == 1
    assert state["leds"]["mute3"] == 127


def test_fader_echo_updates_state_and_wait_blocks(host, client):
    seq = client.state()["seq"]
    result = {}

    def waiter():
        result["reply"] = client.wait("fader", timeout_ms=4000, since=seq, strip=2)

    thread = threading.Thread(target=waiter)
    thread.start()
    time.sleep(0.3)
    assert thread.is_alive(), "wait returned before the echo was sent"
    host.fader(2, 8000)
    thread.join(6)
    assert not thread.is_alive()
    reply = result["reply"]
    assert not reply["timed_out"]
    assert reply["events"][0]["kind"] == "fader"
    assert reply["events"][0]["strip"] == 2 and reply["events"][0]["value14"] == 8000
    host.fader(8, 1234)
    state = wait_state(client, lambda s: s["master_fader14"] == 1234)
    assert state["strips"][2]["fader14"] == 8000
    assert state["master_fader14"] == 1234


def test_wait_times_out_cleanly(client):
    seq = client.state()["seq"]
    t0 = time.monotonic()
    reply = client.wait("meter", timeout_ms=200, since=seq, strip=7)
    assert reply["timed_out"] is True and reply["events"] == []
    assert time.monotonic() - t0 < 2.0


def test_meters_update_strips(host, client):
    host.meter(3, 0xA)
    host.clip(3, True)
    state = wait_state(client, lambda s: s["strips"][3]["meter_clip"] is True)
    assert state["strips"][3]["meter_level"] == 10
    assert state["strips"][3]["meter_clip"] is True
    assert state["strips"][3]["meter_ts"] is not None
    host.clip(3, False)
    state = wait_state(client, lambda s: s["strips"][3]["meter_clip"] is False)
    assert state["strips"][3]["meter_level"] == 10
    client.call("reset_meters")
    assert client.state()["state"]["strips"][3]["meter_level"] == 0


def test_vpot_ring_time_and_assignment(host, client):
    host.vpot_ring(1, 0x46)
    host.time_digits("  1. 1. 1.  0")
    host.assign("PN")
    state = wait_state(client, lambda s: s["assign_text"] == "PN" and s["time_text"].strip() != "")
    ring = state["strips"][1]["vpot"]
    assert ring["center"] is True and ring["mode_name"] == "single" and ring["pos"] == 6
    assert state["time_text"] == "  1. 1. 1.  0"
    assert state["assign_text"] == "PN"
    host.sysex([0x10] + [p.seg7_encode(c) for c in "0123456789"])
    host.sysex([0x11, p.seg7_encode("E"), p.seg7_encode("Q")])
    state = wait_state(client, lambda s: s["assign_text"] == "EQ")
    assert state["time_text"] == "0123456789"


def test_misc_sysex_is_stored(host, client):
    host.sysex([0x0A, 0x01])
    host.sysex([0x0E, 0x02, 0x03])
    host.sysex([0x20, 0x01, 0x07])
    host.sysex([0x21, 0x01])
    state = wait_state(client, lambda s: s["global_meter"] is True and "fader_touch_sensitivity" in s["misc"])
    assert state["misc"]["transport_click"] == [1]
    assert state["misc"]["fader_touch_sensitivity"] == [2, 3]
    assert state["meter_modes"]["1"] == 7


# --- client -> host --------------------------------------------------------------

def test_fader_command_arrives_with_touch_around_it(host, client):
    start = host.count
    reply = client.call("fader", strip=2, value14=8000, touch=True)
    assert reply["value14"] == 8000
    assert host.wait_for(lambda m: m == [0x90, 0x6A, 0x00], timeout=3.0, skip=start) is not None
    relevant = [m for m in host.messages_since(start) if m[0] in (0x90, 0xE2)]
    assert relevant == [[0x90, 0x6A, 0x7F], [0xE2, 0x40, 0x3E], [0x90, 0x6A, 0x00]]
    start = host.count
    client.call("fader", strip="master", value14=16383, touch=False)
    assert host.wait_for(lambda m: m == [0xE8, 0x7F, 0x7F], timeout=3.0, skip=start) is not None
    assert not any(m[:2] == [0x90, 0x70] for m in host.messages_since(start))


def test_press_play_arrives_as_note_on_then_off(host, client):
    start = host.count
    reply = client.press("play")
    assert reply["note"] == 0x5E and reply["name"] == "play"
    assert host.wait_for(lambda m: m == [0x90, 0x5E, 0x00], timeout=3.0, skip=start) is not None
    notes = [m for m in host.messages_since(start) if m[0] == 0x90 and m[1] == 0x5E]
    assert notes == [[0x90, 0x5E, 0x7F], [0x90, 0x5E, 0x00]]
    start = host.count
    client.press("stop", hold_ms=0, modifiers=["shift"])
    assert host.wait_for(lambda m: m == [0x90, 0x46, 0x00], timeout=3.0, skip=start) is not None
    notes = [m for m in host.messages_since(start) if m[0] == 0x90]
    assert notes == [[0x90, 0x46, 0x7F], [0x90, 0x5D, 0x7F], [0x90, 0x5D, 0x00], [0x90, 0x46, 0x00]]


def test_vpot_vpush_jog(host, client):
    start = host.count
    client.call("vpot", strip=0, delta=20)
    client.call("jog", delta=-2)
    client.call("vpush", strip=5, hold_ms=0)
    assert host.wait_for(lambda m: m == [0x90, 0x25, 0x00], timeout=3.0, skip=start) is not None
    msgs = [m for m in host.messages_since(start) if m[0] in (0xB0, 0x90)]
    assert msgs == [[0xB0, 0x10, 0x0F], [0xB0, 0x10, 0x05], [0xB0, 0x3C, 0x41], [0xB0, 0x3C, 0x41],
                    [0x90, 0x25, 0x7F], [0x90, 0x25, 0x00]]


def test_raw_primitives(host, client):
    start = host.count
    client.call("note", n="bank_left", vel=0x7F)
    client.call("cc", n=0x3C, v=0x01)
    client.call("pb", strip=1, value14=300)
    client.call("sysex", bytes=[0x13, 0x00])
    client.call("send", bytes=[0x90, 0x2E, 0x00])
    assert host.wait_for(lambda m: m == [0x90, 0x2E, 0x00], timeout=3.0, skip=start) is not None
    msgs = host.messages_since(start)
    assert [0x90, 0x2E, 0x7F] in msgs and [0xB0, 0x3C, 0x01] in msgs and [0xE1, 300 & 0x7F, 300 >> 7] in msgs
    assert HDR + [0x13, 0x00, 0xF7] in msgs


def test_destructive_guard_lives_in_cli_not_daemon(client):
    # The daemon is a dumb pipe; the CLI is what asks for --yes.
    assert client.call("press", button="rec1", hold_ms=0)["note"] == 0


# --- robustness -----------------------------------------------------------------

def test_unknown_sysex_and_garbage_do_not_kill_the_daemon(host, client, run_dir):
    host.send(HDR + [0x55, 0x01, 0xF7])
    host.send([0xF0, 0x7E, 0x7F, 0x06, 0x01, 0xF7])
    host.send([0xB0, 0x77, 0x01])
    host.send([0xD0, 0x7F])
    time.sleep(0.2)
    assert client.ping()["pong"] is True
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.connect(str(run_dir / "mcu.sock"))
        sock.sendall(b"this is not json\n")
        reply = json.loads(sock.makefile("rb").readline())
    assert reply["ok"] is False and "JSON" in reply["error"]
    with pytest.raises(DaemonError) as err:
        client.call("no_such_command")
    assert "unknown command" in str(err.value)
    with pytest.raises(DaemonError) as err:
        client.call("press", button="bank_lft")
    assert "bank_left" in str(err.value)
    with pytest.raises(DaemonError):
        client.call("fader", strip=9, value14=0)
    with pytest.raises(DaemonError):
        client.call("wait", **{"for": "bogus"})
    assert client.ping()["pong"] is True
    kinds = {e["kind"] for e in client.events(since=0, max=2000)["events"]}
    assert "sysex_unknown" in kinds and "unknown" in kinds


def test_status_and_events(client, run_dir):
    info = daemon_mod.status(run_dir)
    assert info["running"] and info["responding"]
    assert info["port_name"] == PORT and info["device_id"] == "0x7E"
    assert info["ports_open"] is True and info["handshake"] == "confirmed"
    page = client.events(since=0, max=5)
    assert len(page["events"]) == 5 and page["more"] is True
    assert [e["seq"] for e in page["events"]] == sorted(e["seq"] for e in page["events"])
    assert {"in", "out"} <= {e["dir"] for e in client.events(since=0, max=2000)["events"]}
    assert daemon_mod.log_tail(run_dir, 5)


# --- the CLI end to end -----------------------------------------------------------

def test_cli_status_lcd_strips_json(run_dir, host):
    done = cli(run_dir, "status", "--json")
    assert done.returncode == 0, done.stderr
    data = json.loads(done.stdout)
    assert data["daemon"]["port_name"] == PORT and data["state"]["handshake"] == "confirmed"
    done = cli(run_dir, "--json", "lcd")
    assert done.returncode == 0, done.stderr
    lcd = json.loads(done.stdout)
    assert set(lcd) >= {"top", "bottom", "strips"} and len(lcd["strips"]) == 8
    done = cli(run_dir, "strips", "--json")
    assert done.returncode == 0, done.stderr
    strips = json.loads(done.stdout)
    assert strips[1]["idx"] == 2 and strips[2]["fader14"] == 8000
    done = cli(run_dir, "lcd")
    assert done.returncode == 0 and "Snr" in done.stdout
    done = cli(run_dir, "leds")
    assert done.returncode == 0 and "play" in done.stdout
    done = cli(run_dir, "time")
    assert done.returncode == 0 and "0123456789" in done.stdout


def test_cli_press_and_destructive_guard(run_dir, host):
    start = host.count
    done = cli(run_dir, "press", "Bank Right")
    assert done.returncode == 0, done.stderr
    assert host.wait_for(lambda m: m == [0x90, 0x2F, 0x00], timeout=3.0, skip=start) is not None
    done = cli(run_dir, "press", "record")
    assert done.returncode == 1 and "--yes" in done.stderr
    done = cli(run_dir, "rec", "1", "toggle")
    assert done.returncode == 1 and "--yes" in done.stderr
    done = cli(run_dir, "transport", "record")
    assert done.returncode == 1 and "--yes" in done.stderr
    done = cli(run_dir, "press", "no_such_button")
    assert done.returncode == 1 and "unknown button" in done.stderr


def test_cli_mute_is_idempotent(run_dir, host, client):
    host.led(0x12, 0x7F)
    wait_state(client, lambda s: s["strips"][2]["mute"] == 127)
    start = host.count
    done = cli(run_dir, "mute", "3", "on")
    assert done.returncode == 0 and "already" in done.stdout
    assert not any(m[:2] == [0x90, 0x12] for m in host.messages_since(start))
    done = cli(run_dir, "--timeout", "300", "mute", "3", "off")
    assert done.returncode == 0, done.stderr
    assert host.wait_for(lambda m: m == [0x90, 0x12, 0x00], timeout=3.0, skip=start) is not None


def test_cli_fader_raw_pct_and_db_without_calibration(run_dir, host, tmp_path, monkeypatch):
    start = host.count
    done = cli(run_dir, "--timeout", "300", "fader", "4", "raw:1000")
    assert done.returncode == 0, done.stderr
    assert host.wait_for(lambda m: m == [0xE3, 1000 & 0x7F, 1000 >> 7], timeout=3.0, skip=start) is not None
    done = cli(run_dir, "--timeout", "300", "fader", "master", "pct:50")
    assert done.returncode == 0, done.stderr
    assert host.wait_for(lambda m: m[0] == 0xE8 and p.pitch_bend_value(m) in (8191, 8192), timeout=3.0, skip=start)
    env_cal = tmp_path / "nope.json"
    done = subprocess.run([sys.executable, str(MCU_PY), "--run-dir", str(run_dir), "fader", "1", "-6"],
                          capture_output=True, text=True, timeout=20,
                          env={**os.environ, "MCU_CALIBRATION": str(env_cal)})
    assert done.returncode == 1 and "calibrate" in done.stderr


def test_cli_daemon_not_running_exit_2(tmp_path):
    done = cli(tmp_path / "empty", "status")
    assert done.returncode == 2
    assert "daemon start" in done.stderr
    done = cli(tmp_path / "empty", "daemon", "status")
    assert done.returncode == 2


def test_cli_help_and_launcher():
    assert subprocess.run([sys.executable, str(MCU_PY), "--help"], capture_output=True, text=True).returncode == 0
    for sub in ("daemon", "fader", "press", "auto", "calibrate", "raw"):
        done = subprocess.run([sys.executable, str(MCU_PY), sub, "--help"], capture_output=True, text=True)
        assert done.returncode == 0, sub
    if LAUNCHER.exists():
        done = subprocess.run([str(LAUNCHER), "--help"], capture_output=True, text=True)
        assert done.returncode == 0 and "Mackie" in done.stdout


def test_cli_calibrate_then_db_fader_against_a_simulating_host(run_dir, host, tmp_path):
    """fake_logic in simulate mode shows the level in the LCD while touched, like Logic."""
    sim = FakeHost(PORT, DEVICE_ID, simulate=True)
    sim.open(timeout=5.0)
    try:
        sim.device_query()
        assert sim.wait_for(lambda m: m[:6] == HDR + [0x03], timeout=3.0) is not None
        cal_path = tmp_path / "calibration.json"
        env = {**os.environ, "MCU_CALIBRATION": str(cal_path)}
        done = subprocess.run([sys.executable, str(MCU_PY), "--run-dir", str(run_dir), "calibrate", "3", "--steps", "9"],
                              capture_output=True, text=True, timeout=90, env=env)
        assert done.returncode == 0, done.stderr + done.stdout
        pairs = json.loads(cal_path.read_text())["pairs"]
        assert len(pairs) == 9
        assert pairs[0] == [0, None] and pairs[-1] == [16383, 6.0]
        # steps are round(i * 16383 / 8): 0, 2048, ..., 10239, 12287, ..., 16383
        by_value = dict((v, d) for v, d in pairs)
        assert by_value[12287] == 0.0 and by_value[10239] == -10.0 and by_value[2048] == -50.0
        start = sim.count
        done = subprocess.run([sys.executable, str(MCU_PY), "--run-dir", str(run_dir), "fader", "3", "-6"],
                              capture_output=True, text=True, timeout=30, env=env)
        assert done.returncode == 0, done.stderr + done.stdout
        assert "-6.0" in done.stdout
        moved = sim.wait_for(lambda m: m[0] == 0xE2, timeout=3.0, skip=start)
        # -6 dB interpolates between (10239, -10) and (12287, 0) -> 11058
        assert moved is not None and abs(p.pitch_bend_value(moved) - 11058) <= 2
        assert sim.faders[2] == p.pitch_bend_value(moved)
    finally:
        sim.close()
