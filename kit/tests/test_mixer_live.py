"""Live tests for `logic.py mixer` against the "Claude Scratch" project.

Skipped unless Logic Pro is running with a window whose name contains both
"Claude Scratch" and "Mixer". Every test that changes a strip restores it in a
`finally` block, and the last test asserts the whole Mixer is back at rest:
every fader 0.0 dB, every pan 0, nothing muted / soloed / armed / monitoring.

Run:  ~/.claude/skills/logic-atmos-engineer/kit/.venv/bin/python -m pytest kit/tests -v
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

KIT = Path(__file__).resolve().parent.parent
LOGIC = KIT / "logic.py"
STRIP = "Audio 8"


def logic(*args, check=True):
    """Run `logic.py <args> --json`, returning parsed JSON (or raising with stderr)."""
    proc = subprocess.run(
        [sys.executable, str(LOGIC), *args, "--json"],
        capture_output=True, text=True, timeout=120,
    )
    if check and proc.returncode != 0:
        raise AssertionError(f"logic.py {' '.join(args)} failed ({proc.returncode}):\n{proc.stderr}\n{proc.stdout}")
    if not check:
        return proc
    return json.loads(proc.stdout)


def scratch_mixer_open():
    try:
        status = logic("status")
    except AssertionError:
        return False
    return status.get("running") and any(
        "Claude Scratch" in w and "Mixer" in w for w in status.get("windows", [])
    )


pytestmark = pytest.mark.skipif(
    not scratch_mixer_open(),
    reason='needs Logic Pro running with the "Claude Scratch - Mixer" window open',
)


def modal_dialogs():
    """Modal dialogs Logic is showing; setters refuse to run while one is up."""
    sys.path.insert(0, str(KIT))
    import logic as mod  # noqa: E402
    try:
        return mod.modal_dialogs()
    except SystemExit as e:
        return [str(e)]


def require_no_modal():
    dialogs = modal_dialogs()
    if dialogs:
        pytest.skip(f"Logic is showing a modal dialog, setters cannot be verified: {dialogs}")


def db_of(strip):
    value = logic("mixer", "get", strip, "db")["db"]
    return float("-inf") if value == "-inf" else value


# --- read side ---------------------------------------------------------------

def test_window_is_the_scratch_mixer():
    assert "Claude Scratch" in logic("mixer", "window")["window"]


def test_strips_lists_audio_1_to_8():
    strips = logic("mixer", "strips")
    names = [s["name"] for s in strips]
    assert names[:8] == [f"Audio {i}" for i in range(1, 9)], names
    for s in strips[:8]:
        assert s["db"] is not None
        assert s["mute"] in ("on", "off")
        assert s["output"] is not None


def test_get_fields():
    info = logic("mixer", "get", STRIP)
    for field in ("name", "db", "raw", "pan", "mute", "solo", "rec", "mon", "auto", "output", "mode", "peak"):
        assert field in info, field
    assert info["name"] == STRIP
    assert isinstance(info["db"], (int, float)) or info["db"] == "-inf"
    assert isinstance(info["pan"], int)
    # by index too: Audio 8 is the 8th layout item
    assert logic("mixer", "get", "8", "name")["name"] == STRIP
    assert logic("mixer", "get", STRIP, "mute")["mute"] in ("on", "off")


def test_get_unknown_strip_lists_names():
    proc = logic("mixer", "get", "No Such Track", check=False)
    assert proc.returncode == 1
    assert "Audio 1" in proc.stderr and "Audio 8" in proc.stderr


def test_peak_returns_string():
    peak = logic("mixer", "peak", STRIP)
    assert list(peak) == [STRIP]
    assert isinstance(peak[STRIP], str) and peak[STRIP]
    everything = logic("mixer", "peak")
    assert STRIP in everything and isinstance(everything[STRIP], str)


def test_auto_reads_mode():
    info = logic("mixer", "auto", STRIP)
    assert info["auto"] in ("Read", "Touch", "Latch", "Write", "Trim", "Off", "Relative")


# --- closed-loop setters -----------------------------------------------------

def test_set_volume_round_trip():
    require_no_modal()
    assert db_of(STRIP) == 0.0, "test expects the scratch project at rest"
    try:
        r = logic("mixer", "set", STRIP, "volume", "-6.0")
        assert r["ok"], r
        assert abs(r["after"] - (-6.0)) <= 0.1, r
        assert abs(db_of(STRIP) - (-6.0)) <= 0.1
        assert r["steps"] > 0
    finally:
        back = logic("mixer", "set", STRIP, "volume", "0.0")
        assert back["ok"] and back["after"] == 0.0, back
        assert db_of(STRIP) == 0.0


def test_set_pan_round_trip():
    require_no_modal()
    assert logic("mixer", "get", STRIP, "pan")["pan"] == 0
    try:
        r = logic("mixer", "set", STRIP, "pan", "-20")
        assert r["ok"] and r["after"] == -20, r
        assert logic("mixer", "get", STRIP, "pan")["pan"] == -20
    finally:
        back = logic("mixer", "set", STRIP, "pan", "0")
        assert back["ok"] and back["after"] == 0, back
        assert logic("mixer", "get", STRIP, "pan")["pan"] == 0


def test_mute_round_trip():
    require_no_modal()
    assert logic("mixer", "get", STRIP, "mute")["mute"] == "off"
    try:
        r = logic("mixer", "mute", STRIP, "on")
        assert r["ok"] and r["before"] == "off" and r["after"] == "on", r
        assert logic("mixer", "get", STRIP, "mute")["mute"] == "on"
        # idempotent: asking for the state it already has clicks nothing
        again = logic("mixer", "mute", STRIP, "on")
        assert again["ok"] and again["after"] == "on"
    finally:
        back = logic("mixer", "mute", STRIP, "off")
        assert back["ok"] and back["after"] == "off", back
        assert logic("mixer", "get", STRIP, "mute")["mute"] == "off"


def test_rec_requires_yes():
    proc = logic("mixer", "rec", STRIP, "on", check=False)
    assert proc.returncode == 1 and "--yes" in proc.stderr
    assert logic("mixer", "get", STRIP, "rec")["rec"] == "off"


# --- end state ---------------------------------------------------------------

def test_zz_mixer_left_at_rest():
    strips = logic("mixer", "strips")
    problems = []
    for s in strips:
        if s["db"] != 0.0:
            problems.append(f"{s['name']} volume {s['db']} dB")
        if s["pan"] not in (0, None):
            problems.append(f"{s['name']} pan {s['pan']}")
        for control in ("mute", "solo", "rec", "mon"):
            if s[control] == "on":
                problems.append(f"{s['name']} {control} on")
    assert not problems, problems
