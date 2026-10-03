"""Fresh service contract tests; all events and outcomes are synthetic."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from tempoledger.agent.controller import SchedulingAgent
from tempoledger.config import Settings, settings
from tempoledger.exceptions import AgentError, ConfigurationError, SMSError
from tempoledger.models.events import EventType, SchedulingEvent
from tempoledger.models.sessions import SessionContext
from tempoledger.services.alerting import AlertingService, AlertSeverity
from tempoledger.services.retry import add_jitter, retry_with_backoff
from tempoledger.services.sms import MockSMSGateway, SMSGateway, get_sms_gateway
from tempoledger.services.telemetry import TelemetryService

SESSION = UUID(int=1)


@pytest.mark.parametrize(
    "kind,tools",
    [
        (EventType.OPERATOR_FLAGGED, ["pause_session", "alert_operator"]),
        (EventType.REPLY_RECEIVED, ["reschedule_queue"]),
        (EventType.PATTERN_ANOMALY, ["adjust_schedule"]),
        (EventType.DELIVERY_CONFIRMED, []),
    ],
)
def test_agent_is_log_only(kind, tools, monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "synthetic-unused")
    agent = SchedulingAgent()
    agent._make_decision_with_openai = AsyncMock(side_effect=AssertionError("No live model"))
    context = SessionContext(
        session_id=SESSION, campaign_name="Synthetic maintenance", total_messages=3
    )
    before = context.model_dump()
    event = SchedulingEvent(event_type=kind, session_id=SESSION, metadata={"synthetic": True})
    result = asyncio.run(agent.handle_event(event, context))
    assert result.tools_called == tools
    assert context.model_dump() == before
    assert all(d["logged"] and not d["implemented"] and not d["success"] for d in result.decisions)
    agent._make_decision_with_openai.assert_not_called()


def test_memory_interface_records_proposals():
    memory = MagicMock()
    memory.update_session = AsyncMock()
    memory.get_session_context = AsyncMock(
        return_value=SessionContext(session_id=SESSION, campaign_name="Synthetic", total_messages=1)
    )
    agent = SchedulingAgent(memory)
    asyncio.run(
        agent.handle_event(SchedulingEvent(event_type=EventType.REPLY_RECEIVED, session_id=SESSION))
    )
    memory.update_session.assert_awaited_once()
    result = memory.update_session.call_args.args[1]["last_action"]["decisions"][0]
    assert result["implemented"] is False


def test_direct_model_call_is_gated():
    event = SchedulingEvent(event_type=EventType.MESSAGE_QUEUED, session_id=SESSION)
    with pytest.raises(AgentError, match="disabled"):
        asyncio.run(SchedulingAgent()._make_decision_with_openai("Synthetic", event))


def test_gateway_credentials_alone_do_not_enable_delivery(monkeypatch):
    for key in ["twilio_account_sid", "twilio_auth_token", "twilio_phone_number"]:
        monkeypatch.setattr(settings, key, "synthetic-unused")
    assert isinstance(get_sms_gateway(), MockSMSGateway)
    with pytest.raises(ConfigurationError, match="disabled"):
        SMSGateway()


def test_simulated_gateway_status_progression_and_unknown_id():
    async def exercise():
        gateway = MockSMSGateway(Settings(_env_file=None))
        result = await gateway.send_message("synthetic:fixture", "Synthetic queue event", SESSION)
        assert result["status"] == "queued" and result["simulated"]
        assert result["price"] is None
        await gateway._simulate_delivery(result["sid"], delay=0)
        status = await gateway.get_message_status(result["sid"])
        assert status["status"] == "sent" and status["simulated"]
        with pytest.raises(SMSError):
            await gateway.get_message_status("synthetic-missing")

    asyncio.run(exercise())


def test_retry_counts_and_exhaustion(monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", sleep)
    operation = AsyncMock(side_effect=[ConnectionError("synthetic"), "done"])
    wrapped = retry_with_backoff(initial_delay=0, jitter=False)(operation)
    assert asyncio.run(wrapped()) == "done"
    assert operation.await_count == 2 and sleep.await_count == 1
    operation = AsyncMock(side_effect=TimeoutError("synthetic"))
    with pytest.raises(TimeoutError):
        asyncio.run(retry_with_backoff(max_attempts=2, initial_delay=0)(operation)())
    assert operation.await_count == 2
    assert 9 <= add_jitter(10) <= 11


def test_alert_channel_fanout_and_failure_isolation():
    broken = MagicMock()
    broken.send.side_effect = RuntimeError("Synthetic failure")
    good = MagicMock()
    service = AlertingService([broken, good])
    service.send_alert(AlertSeverity.WARNING, "Synthetic operator notice")
    good.send.assert_called_once()


def test_telemetry_records_attributes_without_exporter(monkeypatch):
    service = TelemetryService()
    service.messages_sent = MagicMock()
    service.record_message_sent("synthetic-event", "synthetic-session", "simulated")
    service.messages_sent.add.assert_called_once_with(
        1,
        attributes={
            "message_id": "synthetic-event",
            "session_id": "synthetic-session",
            "delivery_status": "simulated",
        },
    )
    service.pattern_health_histogram = MagicMock()
    service.record_pattern_health(0.3, "synthetic-session")
    assert service._pattern_health_values["synthetic-session"][0] == 0.3
    service.operator_flags = MagicMock()
    from tempoledger.services import telemetry

    monkeypatch.setattr(telemetry.alerting, "send_alert", MagicMock())
    service.record_operator_flag("synthetic-event", "synthetic-session")
    telemetry.alerting.send_alert.assert_called_once()
    service.record_scheduling_decision("synthetic-event", datetime.now(UTC), {"had_pause": True})
