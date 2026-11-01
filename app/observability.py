"""Request IDs, JSON logs and Prometheus metrics.

Every request gets an ID: the caller's X-Request-ID when it sent a usable
one (nginx and most ingresses do), otherwise a new one. It is returned in
the response and written on every log line of that request, so a user's
error report can be matched to the log lines that explain it.

    LOG_FORMAT=text   default, readable in a terminal (uvicorn's access log)
    LOG_FORMAT=json   one JSON object per line, for Loki, ELK or CloudWatch;
                      the access log comes from this module instead

GET /metrics serves Prometheus metrics. Requests are labelled with the
route template (/api/v1/restaurants/{restaurant_id}), never the raw path,
so the number of series stays fixed however many IDs are requested.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import time
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime

from prometheus_client import Counter, Gauge, Histogram

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

_USABLE_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

# Probes and scrapes run every few seconds; logging them hides real traffic.
_QUIET_PATHS = frozenset({"/metrics", "/health/live", "/health/ready"})

access_log = logging.getLogger("tableflow.access")

REQUESTS = Counter(
    "http_requests_total",
    "HTTP requests by route template and status code.",
    ["method", "route", "status"],
)
LATENCY = Histogram(
    "http_request_duration_seconds",
    "Time from the first byte in to the last byte out.",
    ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)
IN_PROGRESS = Gauge("http_requests_in_progress", "Requests being handled now.")
APP_INFO = Gauge("tableflow_app_info", "Always 1; the labels carry the build.", ["version", "env"])


# Set by configure_logging.
_state = {"json_access_log": False}


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    # Fields passed with extra={...} that belong in the JSON line.
    EXTRA = ("method", "path", "route", "status", "duration_ms", "client")

    def format(self, record: logging.LogRecord) -> str:
        line = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        for field in self.EXTRA:
            if hasattr(record, field):
                line[field] = getattr(record, field)
        if record.exc_info:
            line["exc"] = self.formatException(record.exc_info)
        return json.dumps(line, default=str)


def configure_logging(level: str, fmt: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(RequestIdFilter())
    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(levelname)s %(name)s [%(request_id)s] %(message)s")
        )

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    # uvicorn configures its loggers before importing the app; route them
    # through the same handler so every line has one format.
    for name in ("uvicorn", "uvicorn.error"):
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True
    access = logging.getLogger("uvicorn.access")
    if fmt == "json":
        # Replaced by the access line below, which has the request ID.
        access.handlers = []
        access.propagate = False
        access.disabled = True
    else:
        access.disabled = False
        access.handlers = []
        access.propagate = True
    _state["json_access_log"] = fmt == "json"


def _request_id(scope) -> str:
    for name, value in scope.get("headers", []):
        if name == b"x-request-id":
            candidate = value.decode("latin-1")
            if _USABLE_ID.match(candidate):
                return candidate
            break
    return uuid.uuid4().hex


class ObservabilityMiddleware:
    """Outermost ASGI middleware: request ID, access log, metrics."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        rid = _request_id(scope)
        token = request_id_var.set(rid)
        status = 500
        start = time.perf_counter()

        async def send_with_id(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message = {
                    **message,
                    "headers": [*message.get("headers", []), (b"x-request-id", rid.encode())],
                }
            await send(message)

        IN_PROGRESS.inc()
        try:
            await self.app(scope, receive, send_with_id)
        finally:
            IN_PROGRESS.dec()
            elapsed = time.perf_counter() - start
            # The router stores the matched route in the scope.
            route = getattr(scope.get("route"), "path", None) or "unmatched"
            method = scope["method"]
            REQUESTS.labels(method, route, str(status)).inc()
            LATENCY.labels(method, route).observe(elapsed)
            if _state["json_access_log"] and scope["path"] not in _QUIET_PATHS:
                client = scope.get("client")
                access_log.info(
                    "%s %s %s",
                    method,
                    scope["path"],
                    status,
                    extra={
                        "method": method,
                        "path": scope["path"],
                        "route": route,
                        "status": status,
                        "duration_ms": round(elapsed * 1000, 2),
                        "client": client[0] if client else None,
                    },
                )
            request_id_var.reset(token)
