"""Tiny disk cache for fetched data.

Cache-first is the core discipline of this project: every remote source (dfbf,
realkredit) writes raw responses here keyed by date, and is fetched at most once.
Tests mock the network and exercise hit/miss paths against a tmp cache dir.
"""

from __future__ import annotations

from pathlib import Path

# Default cache root: <repo>/data/cache. Overridable for tests.
_DEFAULT_ROOT = Path(__file__).resolve().parents[3] / "data" / "cache"


class Cache:
    """Namespaced key/value store backed by files on disk."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root) if root is not None else _DEFAULT_ROOT

    def _path(self, namespace: str, key: str, ext: str) -> Path:
        safe_key = key.replace("/", "-")
        return self.root / namespace / f"{safe_key}.{ext}"

    def has(self, namespace: str, key: str, ext: str = "json") -> bool:
        return self._path(namespace, key, ext).exists()

    def get(self, namespace: str, key: str, ext: str = "json") -> str | None:
        path = self._path(namespace, key, ext)
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    def put(self, namespace: str, key: str, content: str, ext: str = "json") -> Path:
        path = self._path(namespace, key, ext)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path
