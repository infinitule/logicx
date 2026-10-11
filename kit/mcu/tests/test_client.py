"""Client socket protocol without a live daemon (AF_UNIX temp socket)."""

import json
import socket
import threading
from pathlib import Path

import pytest

from mcu.client import Client, DaemonError, DaemonNotRunning, socket_path


def test_socket_path_falls_back_to_run_dir(tmp_path):
    assert socket_path(tmp_path) == tmp_path / "mcu.sock"


def test_socket_path_reads_daemon_json(tmp_path):
    alt = tmp_path / "short.sock"
    (tmp_path / "daemon.json").write_text(json.dumps({"socket": str(alt)}))
    assert socket_path(tmp_path) == alt


def test_call_missing_socket(tmp_path):
    with pytest.raises(DaemonNotRunning):
        Client(tmp_path).call("ping")


def _serve_once(sock_path: Path, reply: dict):
    """Accept one connection, read a line, write reply."""
    if sock_path.exists():
        sock_path.unlink()
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(sock_path))
    srv.listen(1)
    srv.settimeout(2.0)

    def run():
        try:
            conn, _ = srv.accept()
            with conn:
                buf = b""
                while b"\n" not in buf:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
                conn.sendall(json.dumps(reply).encode() + b"\n")
        finally:
            srv.close()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def test_call_ok_round_trip(tmp_path):
    sock = tmp_path / "mcu.sock"
    _serve_once(sock, {"ok": True, "pong": True})
    reply = Client(tmp_path, timeout=2.0).call("ping")
    assert reply["pong"] is True


def test_call_error_becomes_daemon_error(tmp_path):
    sock = tmp_path / "mcu.sock"
    _serve_once(sock, {"ok": False, "error": "unknown cmd"})
    with pytest.raises(DaemonError, match="unknown cmd"):
        Client(tmp_path, timeout=2.0).call("nope")
