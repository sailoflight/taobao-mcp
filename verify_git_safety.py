"""Fail if machine-local or sensitive runtime files could enter Git."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent
IGNORE_PROBES = (
    ".mcp.json",
    "config.local.toml",
    ".env",
    "user_data/__git_probe__",
    "output/__git_probe__",
    ".venv/__git_probe__",
    "scripts/__git_probe__",
    "skills/taobao-sourcing/sourcing_profile.md",
    "src.zip",
)
FORBIDDEN_TRACKED_FILES = (
    ".mcp.json",
    "config.local.toml",
    ".env",
)
FORBIDDEN_TRACKED_PREFIXES = (
    "user_data/",
    "output/",
    ".venv/",
    "scripts/",
    "skills/taobao-sourcing/sourcing_profile.md",
)
ALLOWED_ENV_EXAMPLES = {".env.public.example"}


def _is_forbidden_tracked_path(path: str) -> bool:
    if path in FORBIDDEN_TRACKED_FILES:
        return True
    if path.startswith(".env.") and path not in ALLOWED_ENV_EXAMPLES:
        return True
    return any(path.startswith(prefix) for prefix in FORBIDDEN_TRACKED_PREFIXES)


def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", f"safe.directory={ROOT}", *args],
        cwd=ROOT,
        check=check,
        text=True,
        capture_output=True,
    )


def main() -> None:
    if _git("rev-parse", "--is-inside-work-tree", check=False).returncode != 0:
        raise SystemExit("Not a Git repository. Run: git init -b main")

    failures = [
        path for path in IGNORE_PROBES
        if _git("check-ignore", "-q", "--", path, check=False).returncode != 0
    ]
    if failures:
        raise SystemExit("Required paths are not ignored: " + ", ".join(failures))

    tracked = set(_git("ls-files").stdout.splitlines())
    leaked = sorted(path for path in tracked if _is_forbidden_tracked_path(path))
    if leaked:
        raise SystemExit("Sensitive or machine-local files are tracked: " + ", ".join(leaked))

    app_manifest_path = ROOT / ".app.json"
    try:
        app_manifest = json.loads(app_manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(".app.json must exist and contain valid development-state JSON") from exc
    if app_manifest != {"apps": {}}:
        raise SystemExit(
            ".app.json is development-only and must remain exactly {\"apps\": {}} "
            "until the user explicitly authorizes adding a real registered app ID."
        )

    print(
        "Git safety checks passed: machine-local data is ignored, and .app.json "
        "contains no registered app ID."
    )


if __name__ == "__main__":
    main()
