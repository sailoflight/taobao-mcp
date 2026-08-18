"""WSL-side stdio<->TCP relay for the Windows-hosted taobao MCP bridge server.

Why this script exists
----------------------
DSH's official MCP plugin (`@deepseek-ai/dsh-mcp-client`, transport=stdio)
spawns a native Linux process and talks JSON-RPC to it over pipes. Spawning
the Windows ``python.exe`` directly through WSL interop is NOT reliable: the
interop pipe reports EOF to the Windows child as soon as the pipe drains, so
the long-lived stdio MCP session dies after the first handshake. This script
therefore keeps the stdio side in WSL and relays it over loopback TCP
(mirrored networking shares 127.0.0.1 between WSL and Windows) to the
persistent Windows-side ``tools/bridge_server.py``.

Critical implementation note
----------------------------
Always read/write the raw file descriptors with ``os.read``/``os.write``.
``sys.stdin.buffer.read(65536)`` (BufferedReader) keeps blocking until it
fills the whole buffer or sees EOF, so a client that writes one JSON-RPC
request and keeps stdin open would never get its bytes relayed. That was the
previous implementation's failure mode.

Usage (as the DSH plugin's stdio command):
    python3 tools/mcp_tcp_bridge.py [port]   # default 8765
"""
from __future__ import annotations

import os
import select
import socket
import sys

HOST = "127.0.0.1"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
BUFFER_SIZE = 65536


def _write_stdout(data: bytes) -> None:
    """Write everything to the MCP stdio stream, handling partial writes."""
    view = memoryview(data)
    while view:
        try:
            written = os.write(sys.stdout.fileno(), view)
        except InterruptedError:
            continue
        except OSError:
            # DSH went away (EPIPE/EBADF) — stop relaying.
            raise
        view = view[written:]


def main() -> int:
    # Fail fast if the Windows bridge service is not running. The DSH plugin
    # owns reconnect/backoff, so we must not loop forever inside this process.
    try:
        sock = socket.create_connection((HOST, PORT), timeout=10)
    except OSError as exc:
        print(
            f"mcp_tcp_bridge: cannot connect to {HOST}:{PORT} ({exc}); "
            "is tools/bridge_server.py running on Windows?",
            file=sys.stderr,
        )
        return 1
    sock.setblocking(False)

    stdin_fd = sys.stdin.fileno()
    stdout_fd = sys.stdout.fileno()
    stdin_open = True          # until the plugin closes its write end
    write_shutdown = False     # sock.shutdown(SHUT_WR) sent exactly once
    pending_out = bytearray()  # stdin bytes not yet handed to the TCP socket

    try:
        while True:
            rlist = [sock]
            wlist: list[int] = []
            if stdin_open:
                rlist.append(stdin_fd)
            if pending_out or (not stdin_open and not write_shutdown):
                wlist.append(sock)

            try:
                readable, writable, _ = select.select(rlist, wlist, [])
            except InterruptedError:
                continue
            except OSError:
                break

            # 1. Drain stdin -> TCP. Use os.read(): it returns whatever is
            #    currently available instead of waiting for a full 64 KiB/EOF.
            if stdin_open and stdin_fd in readable:
                try:
                    data = os.read(stdin_fd, BUFFER_SIZE)
                except InterruptedError:
                    data = None
                except OSError:
                    data = b""
                if data is None:
                    continue
                if data:
                    pending_out.extend(data)
                else:
                    stdin_open = False

            # 2. Flush pending stdin bytes to the Windows bridge. The socket
            #    is non-blocking, so a slow receiver just delays us here.
            if sock in writable and pending_out:
                try:
                    sent = sock.send(pending_out)
                except (BlockingIOError, InterruptedError):
                    pass
                except OSError:
                    break
                else:
                    del pending_out[:sent]

            # Half-close the TCP write side only after all stdin bytes have
            # been delivered, preserving message order for graceful shutdown.
            if (
                sock in writable
                and not stdin_open
                and not pending_out
                and not write_shutdown
            ):
                try:
                    sock.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
                write_shutdown = True

            # 3. Relay TCP -> stdout (Windows server's JSON-RPC responses).
            if sock in readable:
                try:
                    data = sock.recv(BUFFER_SIZE)
                except InterruptedError:
                    data = None
                except OSError:
                    data = b""
                if data is None:
                    continue
                if not data:
                    break
                _write_stdout(data)
    except OSError:
        return 1
    finally:
        try:
            sock.close()
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
