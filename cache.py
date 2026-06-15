from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any


class JsonCache:
    def __init__(self, cache_dir: str | Path, ttl_seconds: int = 900) -> None:
        self.cache_dir = Path(cache_dir)
        self.ttl_seconds = ttl_seconds
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def key_path(self, namespace: str, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
        return self.cache_dir / f"{namespace}_{digest}.json"

    def get(self, namespace: str, key: str) -> Any | None:
        path = self.key_path(namespace, key)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        if time.time() - float(payload.get("_cached_at", 0)) > self.ttl_seconds:
            return None
        return payload.get("data")

    def put(self, namespace: str, key: str, data: Any) -> None:
        path = self.key_path(namespace, key)
        payload = {"_cached_at": time.time(), "data": data}
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

