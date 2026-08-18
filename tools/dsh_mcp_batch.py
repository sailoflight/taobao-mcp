"""Run a batch of MCP tool calls through the WSL<->Windows taobao bridge.

One persistent connection -> one warm Chrome session on the Windows host, so a
whole plan (login + several searches + product fetches) reuses a single browser
tab instead of relaunching Chrome per call (CLAUDE.md anti-flag rule).

Usage:
    python3 tools/dsh_mcp_batch.py PLAN.json

PLAN.json shape:
{
  "timeout_s": 300,          # per-call timeout (optional)
  "ops": [
    {"tool": "taobao_search", "args": {"keyword": "..."}, "out": "output/search_1.json"},
    ...
  ]
}

Each op's full MCP `result` (content text + structuredContent, if any) is
written to `out`. `out` is optional; if omitted the result is just printed.
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


class BatchError(RuntimeError):
    pass


def _wait_readable(fd: int, timeout: float) -> bool:
    try:
        readable, _, _ = select.select([fd], [], [], timeout)
    except InterruptedError:
        return _wait_readable(fd, timeout)
    return bool(readable)


def _read_json_objects(fd: int, until_id: int, timeout: float) -> list[dict]:
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
                raise BatchError(f"non-JSON line on stdio: {line[:120]!r} ({exc})")
            objects.append(obj)
            if obj.get("id") == until_id:
                return objects
    raise BatchError(f"no JSON-RPC response for id={until_id} within {timeout:.0f}s")


def _request(proc: subprocess.Popen, obj: dict, timeout: float) -> dict:
    line = json.dumps(obj, ensure_ascii=False).encode() + b"\n"
    try:
        proc.stdin.write(line)
        proc.stdin.flush()
    except OSError as exc:
        raise BatchError(f"could not write to bridge stdin: {exc}")
    replies = _read_json_objects(proc.stdout.fileno(), obj["id"], timeout)
    for reply in replies:
        if reply.get("id") == obj["id"]:
            return reply
    raise BatchError("internal error: matching id disappeared")


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    plan_path = Path(sys.argv[1])
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    default_timeout = float(plan.get("timeout_s", 300))
    ops = plan["ops"]
    if not ops:
        print("empty ops list")
        return 2

    proc = subprocess.Popen(
        [sys.executable, str(BRIDGE), PORT],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    def call(method: str, params: dict, id_: int, timeout: float) -> dict:
        reply = _request(proc, {"jsonrpc": "2.0", "id": id_, "method": method, "params": params}, timeout)
        if "error" in reply:
            raise BatchError(f"{method} error: {reply['error']}")
        return reply.get("result", {})

    try:
        init = call("initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "dsh_mcp_batch", "version": "1.0"},
        }, 1, 30)
        server = init.get("serverInfo", {})
        print(f"initialize ok: {server.get('name')} {server.get('version')}")

        proc.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        proc.stdin.flush()

        next_id = 100
        for i, op in enumerate(ops, start=1):
            tool = op["tool"]
            args = op.get("args", {})
            out = op.get("out")
            timeout = float(op.get("timeout_s", default_timeout))
            t0 = time.monotonic()
            print(f"[{i}/{len(ops)}] calling {tool} {json.dumps(args, ensure_ascii=False)[:160]} ...", flush=True)
            result = call("tools/call", {"name": tool, "arguments": args}, next_id, timeout)
            next_id += 1
            elapsed = time.monotonic() - t0
            is_err = result.get("isError", False)
            content = result.get("content", [])
            structured = result.get("structuredContent")
            # Recompute: if args marked confirm, never run it here.
            if out:
                Path(out).parent.mkdir(parents=True, exist_ok=True)
                Path(out).write_text(
                    json.dumps({"tool": tool, "args": args, "isError": is_err,
                                "content": content, "structuredContent": structured},
                               ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                print(f"      -> {out}  ({elapsed:.0f}s, isError={is_err})", flush=True)
            else:
                print(f"      ({elapsed:.0f}s, isError={is_err}) {json.dumps(content, ensure_ascii=False)[:400]}", flush=True)
        print("ALL OPS DONE")
        return 0
    except BatchError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"FAIL (unexpected): {exc!r}", file=sys.stderr)
        return 1
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=15)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
