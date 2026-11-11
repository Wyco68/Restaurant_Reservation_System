"""Request IDs, probes, metrics and the JSON log format (app/observability.py)."""

from __future__ import annotations

import json
import logging

from app.observability import JsonFormatter, RequestIdFilter, request_id_var


async def test_every_response_has_a_request_id(client):
    r = await client.get("/health/live")
    assert r.status_code == 200
    assert len(r.headers["x-request-id"]) == 32


async def test_a_usable_caller_id_is_kept(client):
    r = await client.get("/health/live", headers={"X-Request-ID": "trace-42.a_b"})
    assert r.headers["x-request-id"] == "trace-42.a_b"


async def test_an_unusable_caller_id_is_replaced(client):
    r = await client.get("/health/live", headers={"X-Request-ID": "bad id; drop table"})
    assert r.headers["x-request-id"] != "bad id; drop table"


async def test_ready_needs_postgres(client):
    r = await client.get("/health/ready")
    assert r.status_code == 200
    assert r.json()["postgres"] is True


async def test_metrics_label_routes_by_template(client):
    rid = (await client.get("/api/v1/restaurants?limit=1")).json()["items"][0]["id"]
    await client.get(f"/api/v1/restaurants/{rid}")
    await client.get("/no/such/path")

    text = (await client.get("/metrics")).text
    assert 'route="/api/v1/restaurants/{restaurant_id}"' in text
    assert f'route="/api/v1/restaurants/{rid}"' not in text
    assert 'route="unmatched"' in text
    assert "http_request_duration_seconds_bucket" in text


def test_json_lines_carry_the_request_id():
    record = logging.LogRecord("tableflow", logging.INFO, __file__, 1, "booked %s", ("T3",), None)
    record.status = 201
    token = request_id_var.set("abc123")
    try:
        RequestIdFilter().filter(record)
    finally:
        request_id_var.reset(token)

    line = json.loads(JsonFormatter().format(record))
    assert line["msg"] == "booked T3"
    assert line["request_id"] == "abc123"
    assert line["status"] == 201
    assert line["level"] == "INFO"
