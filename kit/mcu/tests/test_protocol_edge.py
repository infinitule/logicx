"""Extra protocol edge cases: decode never raises, framing, truncated LCD dB."""

import pytest

from mcu import protocol as p
from mcu.daemon import validate_midi
from mcu.mcu import parse_db_text


def test_decode_never_raises_on_garbage():
    for junk in ([], [0x00], [0xFF, 0xFF], [0x90], [0x90, 0x40], [0xF0, 0x00], list(range(32))):
        out = p.decode(junk)
        assert out is None or isinstance(out, dict)


def test_decode_device_query_and_lcd():
    query = p.decode([0xF0, 0x00, 0x00, 0x66, 0x14, 0x00, 0xF7])
    assert query is not None
    assert "query" in query["kind"]
    lcd = p.decode(p.lcd_write(56, "-6.0 dB"))
    assert lcd["kind"] == "lcd"
    assert lcd["offset"] == 56


def test_validate_midi_blocks_record_injection():
    with pytest.raises(ValueError, match="7-bit"):
        validate_midi([0x90, 0xDF, 0x7F])


def test_validate_midi_blocks_unterminated_sysex():
    with pytest.raises(ValueError, match="F7"):
        validate_midi([0xF0, 0x00, 0x00, 0x66, 0x14, 0x00])


def test_validate_midi_accepts_well_formed():
    validate_midi([0x90, 0x5E, 0x7F])
    validate_midi([0xE0, 0x00, 0x40])
    validate_midi([0xD0, 0x0A])
    validate_midi([0xF0, 0x00, 0x00, 0x66, 0x14, 0x00, 0xF7])


def test_parse_db_text_accepts_truncated_d():
    assert parse_db_text("-57.4 d") == pytest.approx(-57.4)
    assert parse_db_text("-6.0 dB") == pytest.approx(-6.0)
    with pytest.raises(ValueError):
        parse_db_text("Kick")
