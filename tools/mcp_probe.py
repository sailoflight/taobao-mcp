"""Minimal end-to-end probe for the WSL<->Windows taobao MCP bridge.

Runs the exact DSH client path without needing DSH or the `mcp` package:
spawns `tools/mcp_tcp_bridge.py` as a stdio child, performs a real JSON-RPC
MCP handshake through it, calls `taobao_session(action="status")`, then idles
with the connection open to prove the persistent link survives quiet periods.

Exit code 0 = bridge chain healthy.
"""
from __future__ import annotations

import json
import os
import select
import subprocess
import sys
import time
from pathlib import Path

PORT = "8765"
BRIDGE = Path(__file__).resolve().with_name("mcp_tcp_bridge.py")
TIMEOUT = 15.0


class ProbeError(RuntimeError):
    pass


def _wait_readable(fd: int, timeout: float) -> bool:
    try:
        readable, _, _ = select.select([fd], [], [], timeout)
    except InterruptedError:
        return _wait_readable(fd, timeout)
    return bool(readable)


def _read_json_objects(fd: int, until_id: int, timeout: float = TIMEOUT) -> list[dict]:
    """Read newline-delimited JSON-RPC messages until `until_id` is seen."""
    buffer = b""
    deadline = time.monotonic() + timeout
    objects: list[dict] = []
    while time.monotonic() < deadline:
        if b"\n" not in buffer:
            if not _wait_readable(fd, max(0.0, deadline - time.monotonic())):
                break
            try:
                chunk = os.read(fd, 65536)
            except InterruptedError:
                continue
            except OSError:
                break
            if not chunk:
                break
            buffer += chunk
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ProbeError(f"non-JSON line on stdio: {line[:120]!r} ({exc})")
            objects.append(obj)
            if obj.get("id") == until_id:
                return objects
    raise ProbeError(
        f"no JSON-RPC response for id={until_id} within {timeout}s; "
        f"got {len(objects)} object(s)"
    )


def _request(proc: subprocess.Popen, obj: dict) -> dict:
    line = json.dumps(obj, ensure_ascii=False).encode() + b"\n"
    try:
        proc.stdin.write(line)
        proc.stdin.flush()
    except OSError as exc:
        raise ProbeError(f"could not write to bridge stdin: {exc}")
    replies = _read_json_objects(proc.stdout.fileno(), obj["id"])
    for reply in replies:
        if reply.get("id") == obj["id"]:
            if "error" in reply:
                raise ProbeError(f"JSON-RPC error: {reply['error']}")
            return reply
    raise ProbeError("internal probe error: matching id disappeared")


def main() -> int:
    if not BRIDGE.is_file():
        print(f"bridge script not found: {BRIDGE}", file=sys.stderr)
        return 1

    proc = subprocess.Popen(
        [sys.executable, str(BRIDGE), PORT],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        init = _request(proc, {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "mcp_probe", "version": "1.0"},
            },
        })
        server = init.get("result", {}).get("serverInfo", {})
        print(f"initialize ok: {server.get('name')} {server.get('version')}")
        instructions = init.get("result", {}).get("instructions") or ""
        print(f"runtime prompt delivered via initialize.instructions: {'yes' if instructions else 'no'} "
              f"({len(instructions)} chars)")
        if not instructions:
            raise ProbeError("initialize returned no instructions (runtime prompt missing)")

        proc.stdin.write(
            b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n'
        )
        proc.stdin.flush()

        listing = _request(proc, {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {},
        })
        tools = listing.get("result", {}).get("tools", [])
        names = sorted(tool["name"] for tool in tools)
        print(f"tools/list ok: {len(tools)} tools")
        for name in names:
            print(f"  - {name}")
        if not any(name.startswith("taobao_") for name in names):
            raise ProbeError("no taobao_* tools returned")

        status = _request(proc, {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "taobao_session",
                "arguments": {"action": "status"},
            },
        })
        text = status.get("result", {}).get("content", [{}])[0].get("text", "")
        print(f"tools/call taobao_session(action=status) ok: {text!r}")
        if status.get("result", {}).get("isError"):
            raise ProbeError("taobao_session(action=status) returned isError=true")

        # The point of the bridge: survive a quiet period with stdin still open.
        idle_seconds = 12
        print(f"idle {idle_seconds}s with connection open...")
        time.sleep(idle_seconds)
        if proc.poll() is not None:
            raise ProbeError(
                f"bridge exited during idle (rc={proc.returncode}); "
                "persistent connection is still broken"
            )
        print(f"idle-ok: bridge stayed alive for >= {idle_seconds}s")

        # Clean shutdown through the normal EOF path: plugin closes stdin,
        # bridge half-closes TCP, Windows server child exits.
        proc.stdin.close()
        try:
            rc = proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            raise ProbeError("bridge did not exit after stdin EOF")
        print(f"bridge exited cleanly after stdin EOF (rc={rc})")
        return 0
    except ProbeError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        if proc.poll() is None:
            proc.kill()
        return 1
    except Exception as exc:
        print(f"FAIL (unexpected): {exc!r}", file=sys.stderr)
        if proc.poll() is None:
            proc.kill()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
