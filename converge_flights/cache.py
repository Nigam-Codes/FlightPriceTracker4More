"""Simple JSON-on-disk cache keyed by query.

Both providers are free-tier with tight monthly quotas, so identical searches
should never hit the network twice within a TTL. Each entry is a JSON file
named by a SHA-256 of the query parameters; the stored payload is the raw
provider JSON so it can also feed the optional ``--dump`` raw export.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from converge_flights.config import CacheConfig


class QueryCache:
    """Read-through cache for raw provider responses."""

    def __init__(self, config: CacheConfig) -> None:
        self._config = config
        self._dir = Path(config.directory)
        if config.enabled:
            self._dir.mkdir(parents=True, exist_ok=True)

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    @staticmethod
    def make_key(provider: str, params: dict[str, Any]) -> str:
        """Deterministic cache key from provider + query parameters."""
        blob = json.dumps(
            {"provider": provider, "params": params},
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def _path(self, key: str) -> Path:
        return self._dir / f"{key}.json"

    def get(self, key: str) -> Any | None:
        """Return cached payload if present and not expired, else ``None``."""
        if not self._config.enabled:
            return None
        path = self._path(key)
        if not path.exists():
            return None
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        stored_at = float(record.get("stored_at", 0))
        age_hours = (time.time() - stored_at) / 3600.0
        if age_hours > self._config.ttl_hours:
            return None
        return record.get("payload")

    def set(self, key: str, payload: Any) -> None:
        """Persist ``payload`` under ``key`` with the current timestamp."""
        if not self._config.enabled:
            return
        record = {"stored_at": time.time(), "payload": payload}
        self._path(key).write_text(json.dumps(record, default=str), encoding="utf-8")


__all__ = ["QueryCache"]
