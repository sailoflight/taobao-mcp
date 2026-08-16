"""Cross-platform stdio launcher used by the local Codex MCP configuration."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parent
    server = root / "server.py"
    if not server.is_file():
        raise SystemExit(f"MCP server not found: {server}")

    os.chdir(root)
    os.environ["PYTHONUTF8"] = "1"
    os.environ["MCP_TRANSPORT"] = "stdio"
    os.execv(sys.executable, [sys.executable, str(server)])


if __name__ == "__main__":
    main()
