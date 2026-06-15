from __future__ import annotations

import json
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .base import ProviderUnavailable
from .cache import JsonCache


def get_json(
    url: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    cache: JsonCache | None = None,
    namespace: str = "http",
    rate_limit_seconds: float = 0.0,
) -> Any:
    query = urlencode({k: v for k, v in (params or {}).items() if v is not None})
    full_url = f"{url}?{query}" if query else url
    if cache:
        cached = cache.get(namespace, full_url)
        if cached is not None:
            return cached

    if rate_limit_seconds > 0:
        time.sleep(rate_limit_seconds)

    request = Request(full_url, headers=headers or {"Accept": "application/json"})
    try:
        with urlopen(request, timeout=30) as response:
            content_type = response.headers.get("content-type", "")
            body = response.read().decode("utf-8")
    except HTTPError as exc:
        raise ProviderUnavailable(f"HTTP {exc.code} from {url}") from exc
    except URLError as exc:
        raise ProviderUnavailable(f"Cannot reach {url}: {exc.reason}") from exc

    if "json" not in content_type.lower() and body.lstrip().startswith("<"):
        raise ProviderUnavailable(f"{url} returned HTML, not structured JSON")

    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ProviderUnavailable(f"{url} did not return parseable JSON") from exc

    if cache:
        cache.put(namespace, full_url, data)
    return data

