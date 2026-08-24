"""Unit tests for the CLI's pure helpers (calibration maths, value parsing). No ports."""

import json

import pytest

from mcu import mcu as cli


PAIRS = [[0, None], [2048, -40.0], [8192, -12.0], [12288, 0.0], [16383, 6.0]]


def test_calibration_round_trip_and_clamping():
    cal = cli.Calibration(PAIRS)
    assert cal.db_to_fader(None) == 0
    assert cal.db_to_fader(-6.0) == 10240
    assert cal.db_to_fader(0.0) == 12288
    assert cal.db_to_fader(3.0) == round(12288 + 0.5 * (16383 - 12288))
    assert cal.db_to_fader(-100.0) == 2048        # clamps to the lowest finite point
    assert cal.db_to_fader(99.0) == 16383
    assert cal.fader_to_db(10240) == -6.0
    assert cal.fader_to_db(0) is None
    assert cal.fader_to_db(1000) is None
    assert cal.fader_to_db(16383) == 6.0
    assert cal.fader_to_db(2048) == -40.0
    with pytest.raises(ValueError):
        cli.Calibration([[0, None], [100, None]])


def test_calibration_load_accepts_list_or_object(tmp_path):
    path = tmp_path / "cal.json"
    path.write_text(json.dumps(PAIRS))
    assert cli.Calibration.load(path).db_to_fader(0.0) == 12288
    path.write_text(json.dumps({"pairs": PAIRS, "created": "x"}))
    assert cli.Calibration.load(path).db_to_fader(0.0) == 12288
    with pytest.raises(FileNotFoundError) as err:
        cli.Calibration.load(tmp_path / "missing.json")
    assert "calibrate" in str(err.value)


@pytest.mark.parametrize("text,expected", [
    ("-6.0", -6.0), ("+2.5", 2.5), ("0.0", 0.0), ("-12", -12.0), (" -6.0 dB", -6.0), ("3dB", 3.0),
    ("-inf", None), ("-oo", None), ("-∞", None), ("-Inf dB", None),
])
def test_parse_db_text(text, expected):
    assert cli.parse_db_text(text) == expected


@pytest.mark.parametrize("text", ["C", "L12", "ChEQ", "", "Kick", "1.0k"])
def test_parse_db_text_rejects_non_levels(text):
    with pytest.raises(ValueError):
        cli.parse_db_text(text)


def test_parse_fader_value_raw_and_pct():
    assert cli.parse_fader_value("raw:1000") == (1000, "raw 1000")
    assert cli.parse_fader_value("pct:50")[0] in (8191, 8192)
    assert cli.parse_fader_value("100%")[0] == 16383
    with pytest.raises(SystemExit):
        cli.parse_fader_value("raw:99999")
    with pytest.raises(SystemExit):
        cli.parse_fader_value("pct:101")
    with pytest.raises(SystemExit):
        cli.parse_fader_value("loud")


def test_strip_arg():
    assert cli.strip_arg("1") == 0 and cli.strip_arg("8") == 7 and cli.strip_arg("master") == 8
    with pytest.raises(SystemExit):
        cli.strip_arg("0")
    with pytest.raises(SystemExit):
        cli.strip_arg("master", allow_master=False)
