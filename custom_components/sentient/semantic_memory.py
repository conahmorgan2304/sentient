"""Optional LAN-only semantic reranking of already authorised memory results.

Requires a pre-existing local Ollama-compatible /api/embed service and a model
the user has already installed there. Default off. Never downloads models,
sends files to the internet, or stores embeddings or private text. Query timeout
is short to preserve fast voice replies. Failures use the original results.
"""
from __future__ import annotations

import ipaddress
import math
from typing import Any
from urllib.parse import urlsplit

from homeassistant.helpers.aiohttp_client import async_get_clientsession

MAX_CANDIDATES = 8
MAX_VECTOR_SIZE = 4096


def allowed_endpoint(url: str) -> bool:
    if not isinstance(url, str) or len(url) > 160:
        return False
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        if (parsed.scheme != "http" or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path not in ("", "/api/embed")
                or not host or parsed.port is None or not 1 <= parsed.port <= 65535):
            return False
        if host == "localhost":
            return True
        address = ipaddress.ip_address(host)
        return address.is_private and not (address.is_link_local or address.is_multicast)
    except (ValueError, TypeError):
        return False


def similarity(left: Any, right: Any) -> float | None:
    if (not isinstance(left, list) or not isinstance(right, list)
            or not 0 < len(left) == len(right) <= MAX_VECTOR_SIZE):
        return None
    try:
        a = [float(x) for x in left]
        b = [float(x) for x in right]
        if not all(math.isfinite(x) for x in a + b):
            return None
        denom = math.sqrt(sum(x*x for x in a) * sum(x*x for x in b))
        return sum(x*y for x, y in zip(a, b)) / denom if denom > 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def rank_vectors(rows: list[dict[str, Any]], embeddings: list[Any]) -> list[dict[str, Any]]:
    if len(embeddings) != len(rows) + 1:
        return rows
    scored = [similarity(embeddings[0], emb) for emb in embeddings[1:]]
    if any(score is None for score in scored):
        return rows
    return [row for _, row in sorted(zip(scored, rows), key=lambda p: p[0], reverse=True)]


async def async_rerank(hass: Any, options: dict[str, Any], query: str,
                       allowed_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not options.get("memory_semantic_enabled") or len(allowed_rows) < 2:
        return allowed_rows
    url = str(options.get("memory_semantic_url") or "").rstrip("/")
    model = str(options.get("memory_semantic_model") or "").strip()
    if not allowed_endpoint(url) or not 1 <= len(model) <= 80 or any(c in model for c in "\r\n"):
        return allowed_rows
    selected = allowed_rows[:MAX_CANDIDATES]
    texts = [query[:400], *(str(row.get("text") or "")[:400] for row in selected)]
    try:
        import aiohttp

        session = async_get_clientsession(hass)
        async with session.post(
            url if url.endswith("/api/embed") else url + "/api/embed",
            json={"model": model, "input": texts},
            allow_redirects=False,
            timeout=aiohttp.ClientTimeout(total=2.0),
        ) as response:
            if response.status != 200 or int(response.headers.get("Content-Length", "0")) > 1_000_000:
                return allowed_rows
            body = await response.content.read(1_000_001)
            if len(body) > 1_000_000:
                return allowed_rows
            import json

            payload = json.loads(body)
            vectors = payload.get("embeddings") if isinstance(payload, dict) else None
            if not isinstance(vectors, list):
                return allowed_rows
            return rank_vectors(selected, vectors) + allowed_rows[MAX_CANDIDATES:]
    except (Exception):  # noqa: BLE001 - degraded local embeddings must never break voice
        return allowed_rows
