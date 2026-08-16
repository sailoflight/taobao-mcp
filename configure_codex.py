"""Generate the machine-local `.mcp.json` used by the Codex plugin.

Run this script with the same Python environment that has the project
dependencies installed. The generated file contains absolute paths for the
current clone and is intentionally excluded from Git.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _python_for_clone(root: Path) -> Path:
    override = os.environ.get("TAOBAO_MCP_PYTHON", "").strip()
    candidates = [
        Path(override).expanduser() if override else None,
        root / ".venv" / "Scripts" / "python.exe",
        root / ".venv" / "bin" / "python",
        Path(sys.executable),
    ]
    for candidate in candidates:
        if candidate is not None and candidate.is_file():
            return candidate.resolve()
    raise SystemExit("No usable Python interpreter found. Create .venv or set TAOBAO_MCP_PYTHON.")


def build_config(root: Path) -> dict:
    python = _python_for_clone(root)
    launcher = (root / "run_mcp_stdio.py").resolve()
    if not launcher.is_file():
        raise SystemExit(f"stdio launcher not found: {launcher}")
    return {
        "mcpServers": {
            "taobao": {
                "command": str(python),
                "args": [str(launcher)],
                "cwd": str(root.resolve()),
                "env": {"PYTHONUTF8": "1"},
            }
        }
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate machine-local Codex MCP configuration")
    parser.add_argument("--check", action="store_true", help="print the configuration without writing it")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent
    config = build_config(root)
    rendered = json.dumps(config, ensure_ascii=False, indent=2) + "\n"
    if args.check:
        print(rendered, end="")
        return

    output = root / ".mcp.json"
    output.write_text(rendered, encoding="utf-8")
    print(f"Wrote machine-local Codex MCP config: {output}")
    print("This file is intentionally ignored by Git; rerun this script on every machine.")


if __name__ == "__main__":
    main()
