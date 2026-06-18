"""Lightweight configuration: load an untracked .env into the environment.

Dependency-free (no python-dotenv). Existing environment variables win over
.env values, so an explicit ``export`` always overrides the file.
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def load_env(path: str | Path | None = None) -> None:
    """Load ``KEY=VALUE`` lines from a .env file into os.environ (if not set)."""
    env_path = Path(path) if path is not None else REPO_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)
