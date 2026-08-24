"""Regressions for the 2026-08-23 adversarial review of daemon.py."""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from mcu import daemon as d  # noqa: E402


@pytest.mark.parametrize("bad", [
    [0x90, 0xDF, 0x7F],              # 8-bit data byte -> host would see 90 5F 7F (RECORD)
    [0x90, 0x80, 0x7F],              # -> REC1 arm
    [0xE0, 0xFF, 0xFF],
    [0xF0, 0x90, 0xDF, 0x7F, 0xF7],  # status byte inside sysex
    [0xF0, 0x00, 0x00, 0x66, 0x7E, 0x13],  # unterminated sysex poisons the stream
    [0xF7],
    [0x90, 0x5E],                    # short
    [0xC0, 0x01, 0x02],              # program change is 2 bytes
    [0x7F, 0x00, 0x00],              # not a status byte
    [],
])
def test_validate_midi_rejects_unframed(bad):
    with pytest.raises(ValueError):
        d.validate_midi(bad)


@pytest.mark.parametrize("good", [
    [0x90, 0x5E, 0x7F], [0xE0, 0x00, 0x40], [0xD0, 0x1C], [0xB0, 0x10, 0x41],
    [0xF0, 0x00, 0x00, 0x66, 0x14, 0x01, 0x41, 0x42, 0xF7],
])
def test_validate_midi_accepts_framed(good):
    d.validate_midi(good)


def test_pidfile_never_trusts_pid_zero_or_negative(tmp_path):
    for text in ("0", "-1", "1", "garbage", ""):
        (tmp_path / "daemon.pid").write_text(text)
        assert d.read_pid(tmp_path) is None
        assert d.status(tmp_path)["running"] is False
        assert d.stop(tmp_path) is False


def test_pidfile_of_a_foreign_process_is_stale_not_killed(tmp_path):
    # our own (non-daemon) pid: alive, but not a daemon -> treated as stale, never signalled
    (tmp_path / "daemon.pid").write_text(str(os.getpid()))
    assert d.pid_alive(os.getpid())
    assert d.pid_is_daemon(os.getpid()) is False
    assert d.status(tmp_path)["running"] is False
    assert d.stop(tmp_path) is False


def test_eviction_is_reported():
    dm = d.Daemon(run_dir="/tmp/unused-mcu-test", port_name="unused")
    for i in range(d.RING_SIZE + 50):
        with dm.cond:
            dm._record({"kind": "meter", "strip": 0, "level": 1}, "in")
    res = dm.cmd_events(since=0, max=5)
    assert res["evicted"] == 50 and res["first_seq"] == 51
    res2 = dm.cmd_events(since=dm.seq - 3, max=5)
    assert res2["evicted"] == 0
