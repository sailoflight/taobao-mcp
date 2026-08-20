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


def plan_default_timeout(plans_dir: Path) -> float:
    """--dir 模式: 取各 plan 的最大 timeout_s(默认 300)."""
    t = 300.0
    for pf in plans_dir.glob("plan_*.json"):
        try:
            p = json.loads(pf.read_text(encoding="utf-8"))
            t = max(t, float(p.get("timeout_s", 300)))
        except Exception:
            pass
    return t


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
    if len(sys.argv) not in (2, 3):
        print(__doc__)
        return 2

    # 目录合并队列模式(2026-08-20 用户): 把 DIR 下所有 plan_*.json 的 ops 合并成一个
    # 大 plan, 一次连接跑完 — 浏览器只开一次、关一次, 避免多次手动调用间反复开关
    # Chrome(反复开关加深风控)。用法: dsh_mcp_batch.py --dir output/plans
    merged_out_dir: str = ""
    if sys.argv[1] == "--dir":
        plans_dir = Path(sys.argv[2])
        if not plans_dir.is_dir():
            print(f"plans dir not found: {plans_dir}", file=sys.stderr)
            return 2
        merged_out_dir = str(plans_dir / "merged")
        merged_ops: list[dict] = []
        for pf in sorted(plans_dir.glob("plan_*.json")):
            try:
                p = json.loads(pf.read_text(encoding="utf-8"))
                ops = p.get("ops") or []
                for o in ops:
                    o = dict(o)
                    # 合并时把 out 改写到统一 merged 目录下, 避免覆盖/散落
                    if o.get("out"):
                        o["out"] = str(Path(merged_out_dir) / Path(o["out"]).name)
                    merged_ops.append(o)
            except Exception as exc:
                print(f"  skip {pf.name}: {exc}", file=sys.stderr)
        if not merged_ops:
            print(f"no plan_*.json ops found in {plans_dir}", file=sys.stderr)
            return 2
        default_timeout = float(plan_default_timeout(plans_dir))
        ops = merged_ops
        print(f"[--dir] merged {len(merged_ops)} ops from {plans_dir} "
              f"(single connection, browser opens once; outs -> {merged_out_dir})", flush=True)
    else:
        plan_path = Path(sys.argv[1])
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        default_timeout = float(plan.get("timeout_s", 300))
        ops = plan["ops"]
        if not ops:
            print("empty ops list")
            return 2

    # 确保首个 op 是 login(合并模式下自动补), 否则 server 未启动 Chrome 会报 not_started
    first_tool = ops[0].get("tool", "")
    if first_tool != "taobao_session":
        login_out = str(Path(merged_out_dir or "output") / "_auto_login.json")
        ops.insert(0, {
            "tool": "taobao_session", "args": {"action": "login"},
            "out": login_out,
        })

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
