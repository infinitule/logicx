"""Socket client for the Claude MCU daemon. No dependencies.

    from mcu.client import Client, DaemonNotRunning, DaemonError
    c = Client(run_dir)
    c.call("state")["state"]["lcd_top"]
    c.call("press", button="play")
    c.wait("led", timeout_ms=1500, since=seq, note=0x5E)

Wire format: connect to <run_dir>/mcu.sock, send one JSON object on one line
({"cmd": ..., ...args}), read one JSON line back. {"ok": false, "error": ...}
becomes DaemonError; a missing socket or refused connection becomes
DaemonNotRunning (the CLI turns that into exit code 2).
"""

import json
import socket
from pathlib import Path


class DaemonNotRunning(Exception):
    pass


class DaemonError(Exception):
    pass


def socket_path(run_dir):
    """Where the daemon's socket is: what daemon.json records, else <run_dir>/mcu.sock.

    The daemon moves its socket to a short path under the temp dir when the run
    dir is too deep for a unix socket (macOS caps sun_path at 104 bytes).
    """
    run_dir = Path(run_dir)
    try:
        recorded = json.loads((run_dir / "daemon.json").read_text()).get("socket")
        if recorded:
            return Path(recorded)
    except (FileNotFoundError, ValueError, AttributeError):
        pass
    return run_dir / "mcu.sock"


class Client:
    def __init__(self, run_dir, timeout=10.0):
        self.run_dir = Path(run_dir)
        self.timeout = timeout

    @property
    def sock_path(self):
        return socket_path(self.run_dir)

    def call(self, cmd, timeout=None, **args):
        """Send one command, return the decoded reply dict (always has ok=True)."""
        sock_path = self.sock_path
        if not sock_path.exists():
            raise DaemonNotRunning(f"no daemon socket at {sock_path}")
        request = json.dumps({"cmd": cmd, **args}).encode("utf-8") + b"\n"
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(timeout if timeout is not None else self.timeout)
                sock.connect(str(sock_path))
                sock.sendall(request)
                buf = b""
                while b"\n" not in buf:
                    chunk = sock.recv(1 << 16)
                    if not chunk:
                        break
                    buf += chunk
        except (FileNotFoundError, ConnectionRefusedError) as exc:
            raise DaemonNotRunning(f"daemon socket {sock_path} is not answering ({exc})") from exc
        except socket.timeout as exc:
            raise DaemonError(f"{cmd}: no reply from the daemon within {timeout or self.timeout}s") from exc
        if not buf:
            raise DaemonError(f"{cmd}: daemon closed the connection without replying")
        try:
            reply = json.loads(buf.split(b"\n", 1)[0])
        except ValueError as exc:
            raise DaemonError(f"{cmd}: unreadable reply from daemon: {exc}") from exc
        if not isinstance(reply, dict) or not reply.get("ok"):
            raise DaemonError(reply.get("error", "unknown error") if isinstance(reply, dict) else str(reply))
        return reply

    # Thin conveniences; everything is reachable through call().

    def ping(self):
        return self.call("ping", timeout=2.0)

    def state(self):
        return self.call("state")

    def events(self, since=None, max=200):
        return self.call("events", since=since, max=max)

    def wait(self, what="any", timeout_ms=1000, since=None, **filters):
        args = {"for": what, "timeout_ms": timeout_ms, "since": since, **filters}
        return self.call("wait", timeout=timeout_ms / 1000 + 5, **args)

    def press(self, button, hold_ms=None, modifiers=None):
        args = {"button": button}
        if hold_ms is not None:
            args["hold_ms"] = hold_ms
        if modifiers:
            args["modifiers"] = list(modifiers)
        return self.call("press", **args)
