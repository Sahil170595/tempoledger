"""Backend boundaries exercised in-process with a fake database, never infrastructure."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import httpx
import pytest

from tempoledger.api.main import app
from tempoledger.api.middleware.rate_limit import limiter
from tempoledger.config import settings
from tempoledger.exceptions import ConfigurationError
from tempoledger.services.database import db

SESSION = UUID(int=1)


@pytest.fixture
def store(monkeypatch):
    rows = []

    async def execute(query, *args):
        if "INSERT" in query:
            rows.append(
                dict(
                    id=args[0],
                    session_id=args[1],
                    message_content=args[2],
                    scheduled_time=args[3],
                    scheduling_metadata=json.loads(args[4]),
                    actual_send_time=None,
                    delivery_status=None,
                )
            )
        return "INSERT 0 1"

    async def fetch(query, *args):
        if "GROUP BY" in query:
            return (
                [
                    dict(
                        session_id=SESSION,
                        message_count=len(rows),
                        created_at=rows[0]["scheduled_time"],
                    )
                ]
                if rows
                else []
            )
        return [r for r in rows if r["session_id"] == args[0]]

    monkeypatch.setattr(db, "execute", execute)
    monkeypatch.setattr(db, "fetch", fetch)
    monkeypatch.setattr(limiter, "enabled", False)
    return rows


def request(method, path, **kwargs):
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://synthetic.test"
        ) as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(run())


def test_create_retrieve_and_metrics(store):
    response = request(
        "POST",
        "/api/v1/campaigns",
        json={
            "name": "Synthetic inspection queue",
            "total_messages": 3,
            "duration_hours": 1,
            "messages": [{"content": f"Synthetic event {i}"} for i in range(3)],
        },
    )
    assert response.status_code == 201
    campaign_id = response.json()["id"]
    assert len(store) == 3 and all(r["scheduling_metadata"]["wpm_sampled"] for r in store)
    response = request("GET", f"/api/v1/campaigns/{campaign_id}")
    assert response.status_code == 200 and len(response.json()["messages"]) == 3
    store[0]["delivery_status"] = "delivered"
    store[1]["delivery_status"] = "failed"
    stats = request("GET", f"/api/v1/telemetry/health/{campaign_id}").json()["metrics"]
    assert stats["delivery_rate"] == pytest.approx(1 / 3)
    assert stats["bounce_rate"] == pytest.approx(1 / 3)
    assert stats["health_score"] == pytest.approx(0.7 / 3 + 0.3 * 2 / 3)
    assert len(request("GET", "/api/v1/campaigns").json()) == 1


@pytest.mark.parametrize("content,count", [("", 1), ("   ", 1), ("Synthetic", 2)])
def test_invalid_payload_does_not_persist(store, content, count):
    response = request(
        "POST",
        "/api/v1/campaigns",
        json={
            "name": "Synthetic",
            "total_messages": count,
            "duration_hours": 1,
            "messages": [{"content": content}],
        },
    )
    assert response.status_code == 422 and not store


def test_missing_resource_pagination_and_liveness(store):
    assert request("GET", f"/api/v1/campaigns/{SESSION}").status_code == 404
    assert request("GET", f"/api/v1/telemetry/health/{SESSION}").status_code == 404
    assert request("GET", "/api/v1/campaigns?limit=0").status_code == 400
    assert request("GET", "/api/v1/campaigns?offset=-1").status_code == 400
    assert request("GET", "/api/v1/health/live").json() == {"status": "alive"}


def test_equal_intervals_have_zero_cv(store):
    start = datetime(2030, 1, 7, 12, tzinfo=UTC)
    store.extend(
        dict(
            session_id=SESSION,
            scheduled_time=start + timedelta(seconds=60 * i),
            delivery_status=None,
        )
        for i in range(3)
    )
    metrics = request("GET", f"/api/v1/telemetry/health/{SESSION}").json()["metrics"]
    assert metrics["coefficient_of_variation"] == metrics["interval_variance"] == 0
    assert (
        metrics["health_score"] == 0.3
    )  # Pending rows count in denominator; not delivery success.


def test_rate_limit_really_enforced(store, monkeypatch):
    monkeypatch.setattr(limiter, "enabled", True)
    limiter.reset()
    statuses = [
        request(
            "POST",
            "/api/v1/campaigns",
            json={
                "name": "Synthetic",
                "total_messages": 1,
                "duration_hours": 1,
                "messages": [{"content": "Synthetic queue event"}],
            },
        ).status_code
        for _ in range(11)
    ]
    assert statuses[:10] == [201] * 10 and statuses[-1] == 429


def test_worker_dispatch_is_simulated_and_live_dispatch_fails(monkeypatch):
    import asyncpg

    from tempoledger.workers import tasks

    conn = MagicMock()
    conn.fetch = AsyncMock(
        return_value=[dict(id=UUID(int=2), session_id=SESSION, message_content="Synthetic event")]
    )
    conn.close = AsyncMock()
    monkeypatch.setattr(asyncpg, "connect", AsyncMock(return_value=conn))
    monkeypatch.setattr(tasks.send_scheduled_message, "delay", MagicMock())
    tasks.process_message_queue.run()
    assert tasks.send_scheduled_message.delay.call_args.kwargs["to"] == "synthetic:queue"
    conn.close.assert_awaited_once()
    monkeypatch.setattr(settings, "enable_live_delivery", True)
    with pytest.raises(ConfigurationError, match="recipient mapping"):
        tasks.process_message_queue.run()


def test_worker_serializes_delivery_json(monkeypatch):
    from tempoledger.workers import tasks

    gateway = MagicMock()
    gateway.send_message = AsyncMock(return_value={"status": "queued", "simulated": True})
    monkeypatch.setattr(tasks, "get_sms_gateway", lambda: gateway)
    monkeypatch.setattr(db, "pool", MagicMock())
    execute = AsyncMock()
    monkeypatch.setattr(db, "execute", execute)
    tasks.send_scheduled_message.run(
        str(UUID(int=2)), str(SESSION), "synthetic:fixture", "Synthetic event"
    )
    assert json.loads(execute.call_args.args[2])["simulated"]
