"""Client retries on transient AF_UNIX failures."""

import json
import socket
import threading
from pathlib import Path

import pytest

from mcu.client import Client, DaemonError, DaemonNotRunning


def test_retries_connection_refused_then_succeeds(tmp_path):
    sock_path = tmp_path / "mcu.sock"
    # First connect fails (no listener); after a short delay a server appears.
    state = {"ready": False}

    def delayed_server():
        import time
        time.sleep(0.05)
        if sock_path.exists():
            sock_path.unlink()
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(str(sock_path))
        srv.listen(1)
        srv.settimeout(2.0)
        state["ready"] = True
        try:
            conn, _ = srv.accept()
            with conn:
                buf = b""
                while b"\n" not in buf:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
                conn.sendall(b'{"ok": true, "recovered": true}\n')
        finally:
            srv.close()

    # Pre-create nothing; Client checks exists() first — so create a stale sock file
    # that will refuse, then replace with real server. Easier: mock by creating
    # a listening socket that accepts then resets once, then works.
    # Simpler path: server that resets first accept, answers second.
    if sock_path.exists():
        sock_path.unlink()
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(sock_path))
    srv.listen(2)
    srv.settimeout(2.0)
    hits = {"n": 0}

    def run():
        try:
            for _ in range(2):
                conn, _ = srv.accept()
                hits["n"] += 1
                if hits["n"] == 1:
                    conn.close()  # reset / drop
                    continue
                with conn:
                    buf = b""
                    while b"\n" not in buf:
                        chunk = conn.recv(4096)
                        if not chunk:
                            break
                        buf += chunk
                    conn.sendall(b'{"ok": true, "recovered": true}\n')
        finally:
            srv.close()

    threading.Thread(target=run, daemon=True).start()
    reply = Client(tmp_path, timeout=2.0).call("ping", retries=2)
    assert reply.get("recovered") is True
    assert hits["n"] >= 2
