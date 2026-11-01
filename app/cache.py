"""Redis response cache for the public catalogue.

Only GET endpoints whose answer is the same for every user are cached:

    /api/v1/restaurants           search
    /api/v1/restaurants/{id}      detail
    /api/v1/products              catalogue
    /api/v1/products/{id}         one product

Availability is never cached. It changes with every booking, and a stale
answer would offer a table that is already taken.

Invalidation uses a generation number per namespace. Every key contains
the current generation, and a successful write under the namespace adds one
to it, so all older keys stop being read at once and expire on their own.
There is no SCAN and no list of keys to keep in sync. If a bump is missed
(Redis was down during the write), the TTL still bounds how stale a read
can be.

Redis is optional. If it is not configured nothing is cached. If it is
down or slow, requests go to the databases as if there were no cache, and
Redis is skipped for a few seconds so every request does not wait for the
timeout.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from urllib.parse import parse_qsl, urlencode

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.config import settings

log = logging.getLogger(__name__)

KEY_PREFIX = "tf:cache:v1"

# Seconds to skip Redis after an error.
BACKOFF_SECONDS = 5.0

# Larger responses are served but not stored.
MAX_BODY_BYTES = 512 * 1024

# Namespace -> GET paths in it that may be cached.
_CACHEABLE: dict[str, re.Pattern[str]] = {
    "restaurants": re.compile(r"^/api/v1/restaurants(/\d+)?$"),
    "products": re.compile(r"^/api/v1/products(/[^/]+)?$"),
}

# Namespace -> path prefix whose writes invalidate it.
_PREFIXES: dict[str, str] = {
    "restaurants": "/api/v1/restaurants",
    "products": "/api/v1/products",
}

_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

_url: str | None = settings.redis_url or None
_skip_until = 0.0

# One client per event loop, for the same reason as app/db/mongo.py: a
# connection made on one loop fails on another (tests, reloads).
_clients: dict[int, Redis] = {}


def configure(url: str | None) -> None:
    """Point the cache at another Redis, or turn it off with None."""
    global _url, _skip_until
    _url = url or None
    _skip_until = 0.0
    _clients.clear()


def current_url() -> str | None:
    return _url


def _client() -> Redis:
    key = id(asyncio.get_running_loop())
    client = _clients.get(key)
    if client is None:
        client = Redis.from_url(
            _url,
            socket_timeout=settings.redis_timeout_seconds,
            socket_connect_timeout=settings.redis_timeout_seconds,
        )
        _clients[key] = client
    return client


def _available() -> bool:
    return _url is not None and time.monotonic() >= _skip_until


def _trip(exc: Exception) -> None:
    global _skip_until
    _skip_until = time.monotonic() + BACKOFF_SECONDS
    log.warning("redis unavailable, skipping the cache for %.0fs: %s", BACKOFF_SECONDS, exc)


def _generation_key(namespace: str) -> str:
    return f"{KEY_PREFIX}:gen:{namespace}"


def _key(namespace: str, generation: int, path: str, query_string: bytes) -> str:
    # Parameters are sorted so ?a=1&b=2 and ?b=2&a=1 share an entry. The
    # path is hashed so a long query cannot make a long key.
    query = urlencode(sorted(parse_qsl(query_string.decode("latin-1"), keep_blank_values=True)))
    digest = hashlib.sha256(f"{path}?{query}".encode()).hexdigest()[:32]
    return f"{KEY_PREFIX}:{namespace}:{generation}:{digest}"


def _cacheable_namespace(path: str) -> str | None:
    for namespace, pattern in _CACHEABLE.items():
        if pattern.match(path):
            return namespace
    return None


def _write_namespace(path: str) -> str | None:
    for namespace, prefix in _PREFIXES.items():
        if path == prefix or path.startswith(prefix + "/"):
            return namespace
    return None


async def invalidate(namespace: str) -> None:
    """Make every cached entry in the namespace unreachable."""
    if not _available():
        return
    try:
        await _client().incr(_generation_key(namespace))
    except (RedisError, OSError) as exc:
        _trip(exc)


async def ping() -> bool | None:
    """True or False for /health, None when no cache is configured."""
    if _url is None:
        return None
    try:
        return bool(await _client().ping())
    except (RedisError, OSError):
        return False


async def close() -> None:
    for client in _clients.values():
        await client.aclose()
    _clients.clear()


def _add_header(send, value: bytes):
    async def wrapped(message):
        if message["type"] == "http.response.start":
            message = {**message, "headers": [*message.get("headers", []), (b"x-cache", value)]}
        await send(message)

    return wrapped


class ResponseCache:
    """ASGI middleware. Adds X-Cache: HIT, MISS or BYPASS to cacheable GETs."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or _url is None:
            await self.app(scope, receive, send)
            return

        method, path = scope["method"], scope["path"]
        if method == "GET" and (namespace := _cacheable_namespace(path)):
            await self._read(namespace, scope, receive, send)
        elif method in _WRITE_METHODS and (namespace := _write_namespace(path)):
            await self._write(namespace, scope, receive, send)
        else:
            await self.app(scope, receive, send)

    async def _read(self, namespace, scope, receive, send) -> None:
        if not _available():
            await self.app(scope, receive, _add_header(send, b"BYPASS"))
            return
        try:
            client = _client()
            generation = int(await client.get(_generation_key(namespace)) or 0)
            key = _key(namespace, generation, scope["path"], scope.get("query_string", b""))
            body = await client.get(key)
        except (RedisError, OSError) as exc:
            _trip(exc)
            await self.app(scope, receive, _add_header(send, b"BYPASS"))
            return

        if body is not None:
            await send({
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"x-cache", b"HIT"),
                ],
            })
            await send({"type": "http.response.body", "body": body})
            return

        # Miss: stream the response through and keep a copy.
        status = 0
        content_type = b""
        chunks: list[bytes] = []
        size = 0

        async def capture(message):
            nonlocal status, content_type, size
            if message["type"] == "http.response.start":
                status = message["status"]
                content_type = dict(message.get("headers", [])).get(b"content-type", b"")
                message = {**message, "headers": [*message.get("headers", []), (b"x-cache", b"MISS")]}
            elif message["type"] == "http.response.body":
                chunk = message.get("body", b"")
                size += len(chunk)
                if size <= MAX_BODY_BYTES:
                    chunks.append(chunk)
            await send(message)

        await self.app(scope, receive, capture)

        if status == 200 and content_type.startswith(b"application/json") and size <= MAX_BODY_BYTES:
            try:
                await client.set(key, b"".join(chunks), ex=settings.cache_ttl_seconds)
            except (RedisError, OSError) as exc:
                _trip(exc)

    async def _write(self, namespace, scope, receive, send) -> None:
        # Bump before the response leaves, so a client that reads right
        # after its own write never gets the old entry.
        async def bump_on_success(message):
            if message["type"] == "http.response.start" and 200 <= message["status"] < 300:
                await invalidate(namespace)
            await send(message)

        await self.app(scope, receive, bump_on_success)
