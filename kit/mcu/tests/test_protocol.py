"""Byte-exact tests for protocol.py. No MIDI ports needed."""

import pytest

from mcu import protocol as p


# --- button names -------------------------------------------------------------

@pytest.mark.parametrize("name", ["Bank Left", "bank-left", "BANK_LEFT", "bank_left", "0x2E", "46", " bank  left "])
def test_button_name_normalisation(name):
    assert p.button_note(name) == 0x2E


def test_button_table_spot_checks():
    assert p.BUTTONS["rec1"] == 0x00 and p.BUTTONS["rec8"] == 0x07
    assert p.BUTTONS["solo1"] == 0x08 and p.BUTTONS["mute8"] == 0x17
    assert p.BUTTONS["select1"] == 0x18 and p.BUTTONS["vsel8"] == 0x27
    assert p.BUTTONS["f1"] == 0x36 and p.BUTTONS["f8"] == 0x3D
    assert p.BUTTONS["play"] == 0x5E and p.BUTTONS["record"] == 0x5F and p.BUTTONS["stop"] == 0x5D
    assert p.BUTTONS["fader_touch1"] == 0x68 and p.BUTTONS["fader_touch_master"] == 0x70
    assert p.BUTTONS["relay_click"] == 0x76 and p.BUTTONS["rude_solo_led"] == 0x73
    assert p.BUTTON_NAMES[0x2E] == "bank_left"
    assert len(p.BUTTONS) == len(set(p.BUTTONS.values())), "duplicate note numbers"


def test_button_aliases_and_ints():
    assert p.button_note("ff") == 0x5C
    assert p.button_note("Rew") == 0x5B
    assert p.button_note("sel3") == 0x1A
    assert p.button_note(0x5E) == 0x5E


def test_button_unknown_lists_close_matches():
    with pytest.raises(ValueError) as err:
        p.button_note("bank_lft")
    assert "bank_left" in str(err.value)
    with pytest.raises(ValueError):
        p.button_note("0x80")
    with pytest.raises(ValueError):
        p.button_note(200)


def test_destructive_set():
    assert p.DESTRUCTIVE_BUTTONS == set(range(8)) | {0x5F, 0x58, 0x57}
    assert p.BUTTONS["play"] not in p.DESTRUCTIVE_BUTTONS


# --- encoders -----------------------------------------------------------------

def test_note_on_off():
    assert p.note_on(0x5E) == [0x90, 0x5E, 0x7F]
    assert p.note_on(0x5E, 0x40) == [0x90, 0x5E, 0x40]
    assert p.note_off(0x5E) == [0x90, 0x5E, 0x00]


def test_cc():
    assert p.cc(0x10, 0x03) == [0xB0, 0x10, 0x03]


def test_pitch_bend_lsb_first():
    assert p.pitch_bend(0, 0) == [0xE0, 0x00, 0x00]
    assert p.pitch_bend(0, 16383) == [0xE0, 0x7F, 0x7F]
    assert p.pitch_bend(2, 8000) == [0xE2, 0x40, 0x3E]        # 8000 = 0x1F40
    assert p.pitch_bend(7, 1) == [0xE7, 0x01, 0x00]
    assert p.pitch_bend(8, 8192) == [0xE8, 0x00, 0x40]       # master
    assert p.pitch_bend(0, 99999) == [0xE0, 0x7F, 0x7F]      # clamps
    assert p.pitch_bend(0, -5) == [0xE0, 0x00, 0x00]
    assert p.pitch_bend_value([0xE2, 0x40, 0x3E]) == 8000
    with pytest.raises(ValueError):
        p.pitch_bend(9, 0)


def test_fader_touch():
    assert p.fader_touch(0, True) == [0x90, 0x68, 0x7F]
    assert p.fader_touch(7, False) == [0x90, 0x6F, 0x00]
    assert p.fader_touch(8, True) == [0x90, 0x70, 0x7F]


def test_vpot_turn_splitting():
    assert p.vpot_turn(0, 20) == [[0xB0, 0x10, 0x0F], [0xB0, 0x10, 0x05]]
    assert p.vpot_turn(3, -3) == [[0xB0, 0x13, 0x43]]
    assert p.vpot_turn(0, 1) == [[0xB0, 0x10, 0x01]]
    assert p.vpot_turn(0, -15) == [[0xB0, 0x10, 0x4F]]
    assert p.vpot_turn(0, -16) == [[0xB0, 0x10, 0x4F], [0xB0, 0x10, 0x41]]
    assert p.vpot_turn(0, 0) == []
    assert p.vpot_push(5) == [0x90, 0x25, 0x7F]


def test_jog():
    assert p.jog(2) == [[0xB0, 0x3C, 0x01], [0xB0, 0x3C, 0x01]]
    assert p.jog(-1) == [[0xB0, 0x3C, 0x41]]
    assert p.jog(0) == []


def test_sysex_wrapping():
    assert p.sysex([0x01, 0x02]) == [0xF0, 0x00, 0x00, 0x66, 0x14, 0x01, 0x02, 0xF7]
    assert p.sysex([0x00], device_id=0x7E) == [0xF0, 0x00, 0x00, 0x66, 0x7E, 0x00, 0xF7]
    with pytest.raises(ValueError):
        p.sysex([0x80])


def test_handshake_bytes_exact():
    serial = b"CLAUDE1"
    q = p.host_connection_query(serial, [1, 2, 3, 4])
    assert q == [0xF0, 0x00, 0x00, 0x66, 0x14, 0x01,
                 0x43, 0x4C, 0x41, 0x55, 0x44, 0x45, 0x31,
                 0x01, 0x02, 0x03, 0x04, 0xF7]
    assert p.host_connection_confirm(serial) == [
        0xF0, 0x00, 0x00, 0x66, 0x14, 0x03, 0x43, 0x4C, 0x41, 0x55, 0x44, 0x45, 0x31, 0xF7]
    assert p.version_reply() == [0xF0, 0x00, 0x00, 0x66, 0x14, 0x14, 0x56, 0x31, 0x2E, 0x30, 0xF7]
    assert p.device_query() == [0xF0, 0x00, 0x00, 0x66, 0x14, 0x00, 0xF7]
    reply = p.host_connection_reply(serial, [9, 8, 7, 6])
    assert reply[5] == 0x02 and reply[6:13] == list(serial) and reply[13:17] == [9, 8, 7, 6]
    with pytest.raises(ValueError):
        p.host_connection_query(b"SHORT", [1, 2, 3, 4])
    with pytest.raises(ValueError):
        p.host_connection_query(serial, [1, 2, 3])


def test_challenge_response_is_7bit():
    for ch in ([1, 2, 3, 4], [0x7F, 0x7F, 0x7F, 0x7F], [0, 0, 0, 0]):
        assert all(0 <= r <= 0x7F for r in p.challenge_response(ch))


# --- 7-segment ------------------------------------------------------------------

def test_seg7_char_mapping():
    assert p.seg7_char(0x31) == ("1", False)
    assert p.seg7_char(0x31 | 0x40) == ("1", True)
    assert p.seg7_char(0x20) == (" ", False)
    assert p.seg7_char(0x01) == ("A", False)
    assert p.seg7_char(0x00) == ("@", False)
    assert p.seg7_char(0x1F) == ("_", False)
    assert p.seg7_char(0x41) == ("A", True)
    for ch in "0123456789 ABZ-":
        assert p.seg7_char(p.seg7_encode(ch))[0] == ch
        assert p.seg7_char(p.seg7_encode(ch, dot=True)) == (ch, True)


def test_time_digit_order():
    ev = p.decode([0xB0, 0x49, 0x31])
    assert ev == {"kind": "time_digit", "index": 0, "char": "1", "dot": False, "raw": 0x31}
    ev = p.decode([0xB0, 0x40, 0x35 | 0x40])
    assert ev["index"] == 9 and ev["char"] == "5" and ev["dot"] is True


def test_assign_digit_order():
    assert p.decode([0xB0, 0x4B, 0x50])["index"] == 0      # left
    assert p.decode([0xB0, 0x4A, 0x6E])["index"] == 1      # right
    assert p.decode([0xB0, 0x4B, 0x50])["char"] == "P"


# --- decoders -----------------------------------------------------------------

def test_decode_led():
    assert p.decode([0x90, 0x5E, 0x7F]) == {
        "kind": "led", "note": 0x5E, "state": 0x7F, "name": "play", "state_name": "on"}
    assert p.decode([0x90, 0x10, 0x01])["state_name"] == "blink"
    assert p.decode([0x80, 0x10, 0x40])["state"] == 0        # note off = LED off
    assert p.decode([0x90, 0x75, 0x7F])["name"] == "0x75"


def test_decode_fader():
    assert p.decode([0xE3, 0x40, 0x3E]) == {"kind": "fader", "strip": 3, "value14": 8000}
    assert p.decode([0xE8, 0x7F, 0x7F]) == {"kind": "fader", "strip": 8, "value14": 16383}
    assert p.decode([0xE9, 0x00, 0x00])["kind"] == "unknown"


def test_decode_vpot_ring_all_modes():
    assert p.decode_vpot_ring(0x05) == {"center": False, "mode": 0, "mode_name": "single", "pos": 5, "raw": 0x05}
    assert p.decode_vpot_ring(0x17) == {"center": False, "mode": 1, "mode_name": "boost_cut", "pos": 7, "raw": 0x17}
    assert p.decode_vpot_ring(0x6B) == {"center": True, "mode": 2, "mode_name": "wrap", "pos": 11, "raw": 0x6B}
    assert p.decode_vpot_ring(0x30) == {"center": False, "mode": 3, "mode_name": "spread", "pos": 0, "raw": 0x30}
    ev = p.decode([0xB0, 0x32, 0x46])
    assert ev["kind"] == "vpot_ring" and ev["strip"] == 2 and ev["center"] and ev["pos"] == 6


def test_decode_meter():
    assert p.decode([0xD0, 0x3A]) == {"kind": "meter", "strip": 3, "level": 10, "clip": None}
    assert p.decode([0xD0, 0x3E]) == {"kind": "meter", "strip": 3, "level": None, "clip": True}
    assert p.decode([0xD0, 0x3F]) == {"kind": "meter", "strip": 3, "level": None, "clip": False}
    assert p.decode([0xD0, 0x00]) == {"kind": "meter", "strip": 0, "level": 0, "clip": None}
    assert p.decode([0xD0, 0x7C])["strip"] == 7


def test_decode_lcd():
    msg = p.sysex([0x12, 0x38] + list(b"-6.0"))
    assert p.decode(msg) == {"kind": "lcd", "offset": 56, "text": "-6.0"}
    assert p.lcd_write(0, "Kick") == [0xF0, 0x00, 0x00, 0x66, 0x14, 0x12, 0x00, 0x4B, 0x69, 0x63, 0x6B, 0xF7]


def test_decode_handshake_sysex():
    assert p.decode([0xF0, 0x00, 0x00, 0x66, 0x14, 0x00, 0xF7]) == {"kind": "sysex_device_query"}
    reply = p.host_connection_reply(b"CLAUDE1", [9, 8, 7, 6])
    ev = p.decode(reply)
    assert ev["kind"] == "sysex_host_reply" and ev["serial"] == "CLAUDE1" and ev["response"] == [9, 8, 7, 6]
    assert p.decode(p.sysex([0x13, 0x00]))["kind"] == "sysex_version_request"
    assert p.decode(p.sysex([0x0F, 0x7F]))["kind"] == "sysex_offline"


def test_decode_sysex_misc_and_modes():
    ev = p.decode(p.sysex([0x0A, 0x01]))
    assert ev["kind"] == "sysex_misc" and ev["name"] == "transport_click" and ev["data"] == [1]
    ev = p.decode(p.sysex([0x0E, 0x03, 0x02]))
    assert ev["name"] == "fader_touch_sensitivity" and ev["data"] == [3, 2]
    assert p.decode(p.sysex([0x20, 0x02, 0x07])) == {"kind": "sysex_meter_mode", "strip": 2, "mode": 7}
    assert p.decode(p.sysex([0x21, 0x01])) == {"kind": "sysex_global_meter", "on": True}
    assert p.decode(p.sysex([0x61]))["kind"] == "sysex_faders_min"
    assert p.decode(p.sysex([0x62]))["kind"] == "sysex_leds_off"
    assert p.decode(p.sysex([0x63]))["kind"] == "sysex_reset"
    ev = p.decode(p.sysex([0x10] + [0x31, 0x32 | 0x40, 0x33]))
    assert ev["kind"] == "sysex_time_text" and ev["text"] == "123" and ev["digits"][1] == ("2", True)
    # 7-seg has no lower case: "Pn" travels as 10 0E and reads back as "PN".
    ev = p.decode(p.sysex([0x11, 0x10, 0x0E]))
    assert ev["kind"] == "sysex_assign_text" and ev["text"] == "PN"


def test_decode_never_raises():
    assert p.decode([])["kind"] == "unknown"
    assert p.decode([0xF0])["kind"] == "sysex_unknown"
    assert p.decode([0xF0, 0x7E, 0x7F, 0x06, 0x01, 0xF7])["kind"] == "sysex_unknown"
    assert p.decode([0x90])["kind"] == "unknown"
    assert p.decode(["x", None])["kind"] == "unknown"
    assert p.decode([0xF0, 0x00, 0x00, 0x66, 0x14, 0x55, 0x01, 0xF7]) == {
        "kind": "sysex_unknown", "bytes": [0xF0, 0x00, 0x00, 0x66, 0x14, 0x55, 0x01, 0xF7], "id": 0x55}


def test_decode_other_device_id_is_unknown_not_handshake():
    # Logic probes 0x10/0x11/0x15/0x17 too; only our own id is a handshake.
    ev = p.decode([0xF0, 0x00, 0x00, 0x66, 0x10, 0x00, 0xF7])
    assert ev["kind"] == "sysex_unknown" and ev["device_id"] == 0x10
    ev = p.decode([0xF0, 0x00, 0x00, 0x66, 0x14, 0x00, 0xF7], device_id=0x7E)
    assert ev["kind"] == "sysex_unknown" and ev["device_id"] == 0x14
    assert p.decode([0xF0, 0x00, 0x00, 0x66, 0x7E, 0x00, 0xF7], device_id=0x7E)["kind"] == "sysex_device_query"


def test_describe():
    assert "play" in p.describe_event(p.decode([0x90, 0x5E, 0x7F]))
    assert p.describe_outgoing([0x90, 0x5E, 0x7F]) == "press play"
    assert p.describe_outgoing([0x90, 0x6A, 0x00]) == "touch strip 3 off"
    assert p.describe_outgoing([0xE8, 0x00, 0x40]) == "fader master -> 8192"
    assert p.describe_outgoing([0xB0, 0x10, 0x43]) == "vpot strip 1 -3"
    assert "host connection query" in p.describe_outgoing(p.host_connection_query())
