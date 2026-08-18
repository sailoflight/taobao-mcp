"""Windows-side localhost bridge: one TCP connection = one stdio MCP server child.

Run this on the Windows host (once, keeps running):
    C:\\MCP\\taobao-mcp\\.venv\\Scripts\\python.exe C:\\MCP\\taobao-mcp\\tools\\bridge_server.py [port]

WSL (mirrored networking) connects to 127.0.0.1:<port>. Each accepted
connection spawns `run_mcp_stdio.py -> server.py`, and the bridge relays the
child's stdin/stdout over the socket. Child stderr goes to
`output/bridge-server.log` so stdout stays protocol-pure (MCP stdio requires
stdout to carry only JSON-RPC lines).

Single-copy rule
----------------
A persistent Chrome profile can only be held by ONE server process. The
listener therefore accepts ONE client at a time and rejects extra connections
instead of spawning a second browser-holding server. This matches
`DSH_WSL_BRIDGE.md`: do not run DSH and Codex MCP sessions simultaneously.

Child stdout is drained with raw `os.read()` on the pipe fd, NOT
`proc.stdout.read(N)`, which can wait for a full buffer/EOF before forwarding
a partial JSON-RPC response.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYTHON = str(ROOT / ".venv" / "Scripts" / "python.exe")
LAUNCHER = str(ROOT / "run_mcp_stdio.py")
LOG_PATH = ROOT / "output" / "bridge-server.log"
HOST = "127.0.0.1"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
BUFFER_SIZE = 65536

# Only one live client/child pair at a time (see module docstring).
_ACTIVE_LOCK = threading.Lock()


def _log(msg: str) -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except OSError:
        pass


def _pump_tcp_to_stdin(conn: socket.socket, proc: subprocess.Popen) -> None:
    """Relay client bytes to the MCP child; EOF on the socket closes child stdin."""
    try:
        while True:
            data = conn.recv(BUFFER_SIZE)
            if not data:
                break
            proc.stdin.write(data)
            proc.stdin.flush()
    except OSError:
        pass
    finally:
        try:
            proc.stdin.close()
        except OSError:
            pass


def _relay_stdout_to_tcp(conn: socket.socket, proc: subprocess.Popen) -> None:
    """Raw-fd relay of child stdout so partial JSON-RPC lines arrive promptly."""
    try:
        fd = proc.stdout.fileno()
        while True:
            data = os.read(fd, BUFFER_SIZE)
            if not data:
                break
            conn.sendall(data)
    except OSError:
        pass


def handle(conn: socket.socket, addr: tuple) -> None:
    log_fh = None
    proc: subprocess.Popen | None = None
    try:
        log_fh = LOG_PATH.open("ab")
        proc = subprocess.Popen(
            [PYTHON, LAUNCHER],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=log_fh,
            cwd=str(ROOT),
        )
        _log(f"client {addr} -> spawned server pid={proc.pid}")

        threading.Thread(
            target=_pump_tcp_to_stdin,
            args=(conn, proc),
            daemon=True,
            name=f"stdin-pump-{proc.pid}",
        ).start()

        _relay_stdout_to_tcp(conn, proc)
    except OSError as exc:
        _log(f"client {addr} -> relay error: {exc}")
    finally:
        try:
            conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            conn.close()
        except OSError:
            pass

        if proc is not None:
            try:
                proc.stdin.close()
            except OSError:
                pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    _log(f"server pid={proc.pid} did not die after kill")
            _log(f"server pid={proc.pid} exited rc={proc.returncode}")
        if log_fh is not None:
            try:
                log_fh.close()
            except OSError:
                pass
        _ACTIVE_LOCK.release()


def main() -> None:
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((HOST, PORT))
    srv.listen(4)
    print(f"taobao mcp bridge listening on {HOST}:{PORT}", flush=True)
    _log(f"bridge server started on {HOST}:{PORT} (pid={os.getpid()})")

    while True:
        conn, addr = srv.accept()
        if not _ACTIVE_LOCK.acquire(blocking=False):
            _log(f"rejected client {addr}: another MCP session is active "
                 "(Chrome profile is single-tenant; close the other session first)")
            try:
                conn.close()
            except OSError:
                pass
            continue
        try:
            threading.Thread(
                target=handle,
                args=(conn, addr),
                daemon=True,
                name=f"client-{addr[0]}-{addr[1]}",
            ).start()
        except Exception:
            # If the thread could not start, do not leave the socket/child behind.
            _ACTIVE_LOCK.release()
            try:
                conn.close()
            except OSError:
                pass
            raise


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        _log("bridge server stopped by user")
        raise SystemExit(0)
