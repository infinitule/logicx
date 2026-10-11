"""State.apply and handshake stage machine — no MIDI ports."""

import time

import pytest

from mcu import protocol as p
from mcu.state import HANDSHAKE_STAGES, State


def _apply(state, message):
    ev = p.decode(message)
    assert ev is not None
    state.apply(ev, time.time())
    return ev


def test_handshake_stages_progress():
    s = State()
    assert s.handshake == "none"
    assert not s.handshake_complete
    assert not s.online
    s.set_handshake("queried")
    s.set_handshake("replied")
    s.set_handshake("confirmed")
    assert s.handshake_complete
    assert s.online
    assert set(s.handshake_ts) >= {"queried", "replied", "confirmed"}


def test_handshake_rejects_unknown_stage():
    s = State()
    with pytest.raises(ValueError):
        s.set_handshake("connected")
    assert HANDSHAKE_STAGES == ("none", "queried", "replied", "confirmed")


def test_apply_lcd_partial_write():
    s = State()
    ev = _apply(s, p.lcd_write(0, "Kick   "))
    assert ev["kind"] == "lcd"
    assert s.strip_name(0).startswith("Kick")


def test_apply_led_and_fader_echo():
    s = State()
    _apply(s, [0x90, 0x10, 0x7F])  # mute1 LED on
    assert s.strips[0].mute == 0x7F
    _apply(s, p.pitch_bend(2, 8000))
    assert s.strips[2].fader14 == 8000


def test_apply_assignment_display_period_marks_channel_view():
    s = State()
    _apply(s, [0xB0, 0x4B, ord("P")])
    _apply(s, [0xB0, 0x4A, ord("N") | 0x40])
    text = s.assign_text
    assert "P" in text and "N" in text
    assert "." in text  # channel-view marker used by objpan


def test_apply_meter_clip():
    s = State()
    _apply(s, [0xD0, 0x0A])
    assert s.strips[0].meter_level == 10
    _apply(s, [0xD0, 0x0E])
    assert s.strips[0].meter_clip is True
    _apply(s, [0xD0, 0x0F])
    assert s.strips[0].meter_clip is False


def test_snapshot_is_json_friendly():
    s = State()
    s.set_handshake("confirmed")
    snap = s.snapshot()
    assert snap["handshake"] == "confirmed"
    assert len(snap["strips"]) == 8
    assert "lcd_top" in snap
