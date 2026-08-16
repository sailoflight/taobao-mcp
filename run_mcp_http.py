"""Cross-platform launcher for the authenticated public Streamable HTTP server."""

from __future__ import annotations

import os
import sys
from pathlib import Path


REQUIRED_ENV = (
    "MCP_PUBLIC_URL",
    "OAUTH_ISSUER_URL",
    "OAUTH_JWKS_URL",
    "OAUTH_ALLOWED_SUBJECTS",
)


def main() -> None:
    root = Path(__file__).resolve().parent
    server = root / "server.py"
    if not server.is_file():
        raise SystemExit(f"MCP server not found: {server}")

    missing = [name for name in REQUIRED_ENV if not os.environ.get(name, "").strip()]
    if missing:
        raise SystemExit("Missing required public MCP environment variables: " + ", ".join(missing))

    os.chdir(root)
    os.environ["PYTHONUTF8"] = "1"
    os.environ["MCP_TRANSPORT"] = "streamable-http"
    os.environ.setdefault("MCP_HOST", "127.0.0.1")
    os.environ.setdefault("PORT", "8000")
    os.execv(sys.executable, [sys.executable, str(server)])


if __name__ == "__main__":
    main()
