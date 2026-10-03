"""Replay, distributions and explicit characterization of inherited constraint conflicts."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import numpy as np
import pytest
from pydantic import ValidationError

from tempoledger.config import Settings
from tempoledger.models import Campaign, Message, ScheduledMessage
from tempoledger.scheduling.audit import audit_schedule
from tempoledger.scheduling.engine import JitterScheduler

START = datetime(2030, 1, 7, 9, tzinfo=UTC)
SESSION = UUID(int=1)


def campaign(count=12, duration=2):
    return Campaign(
        id=SESSION,
        name="Synthetic queue",
        total_messages=count,
        duration_hours=duration,
        messages=[
            Message(id=UUID(int=i + 10), content=f"Synthetic event {i}") for i in range(count)
        ],
    )


def trace(seed, c=None, start=START):
    return JitterScheduler(seed=seed).schedule_campaign(c or campaign(), start)


def test_seeded_replay_and_no_global_rng_mutation():
    np.random.seed(100)
    before = np.random.get_state()
    a = trace(7)
    after = np.random.get_state()
    assert np.array_equal(before[1], after[1]) and before[2:] == after[2:]
    assert [(s.send_time, s.reasoning) for s in a] == [(s.send_time, s.reasoning) for s in trace(7)]
    assert [s.send_time for s in a] != [s.send_time for s in trace(8)]


@pytest.mark.parametrize("seed", range(10))
def test_campaign_count_sorted_and_bounded(seed):
    c = campaign()
    rows = trace(seed, c)
    assert len(rows) == len(c.messages)
    assert [s.send_time for s in rows] == sorted(s.send_time for s in rows)
    assert all(START <= s.send_time <= START + timedelta(hours=2) for s in rows)
    assert all(s.session_id == c.id for s in rows)


def test_distribution_and_clipped_samples():
    s = JitterScheduler(seed=1)
    for duration in [0.1, 0.5, 1, 6, 24]:
        offsets = s._distribute_messages(25, duration)
        assert len(offsets) == 25 and offsets == sorted(offsets)
        assert all(0 <= x <= duration * 3600 for x in offsets)
    assert all(30 <= s._sample_wpm() <= 80 for _ in range(1000))
    assert all(5 <= s._sample_pause_duration() <= 45 for _ in range(1000))


def test_forced_pause_records_preparation():
    s = JitterScheduler(Settings(_env_file=None, pause_probability=1), seed=2)
    row = s.schedule_message(
        Message(content="Synthetic event"), [], START.replace(hour=12), SESSION
    )
    r = row.reasoning
    assert r["had_pause"] and 5 <= r["pause_duration"] <= 45
    assert r["typing_duration"] == pytest.approx(120 / r["wpm_sampled"] + r["pause_duration"])


def test_single_event_forward_business_projection():
    s = JitterScheduler(seed=10)
    late = START.replace(hour=18)
    row = s.schedule_message(Message(content="Synthetic event"), [], late, SESSION)
    assert row.send_time == (START + timedelta(days=1))
    early = START.replace(hour=6)
    assert s._enforce_business_hours(early) == START
    assert s._enforce_business_hours(START.replace(hour=12)) == START.replace(hour=12)


def test_campaign_without_open_window_is_reported_not_hidden():
    c = campaign(5, 1)
    start = START.replace(hour=20)
    rows = trace(2, c, start)
    violations = audit_schedule(rows, start, start + timedelta(hours=1), Settings(_env_file=None))
    assert any(v["code"] == "outside_business_hours" for v in violations)
    assert not any(v["code"] == "outside_campaign" for v in violations)


def test_inspector_reports_preparation_conflicts_and_bursts():
    rows = [
        ScheduledMessage(
            message=Message(id=UUID(int=i + 10), content="Synthetic event"),
            session_id=SESSION,
            send_time=START,
            reasoning={"typing_duration": 60, "prepared_from": START.isoformat()},
        )
        for i in range(4)
    ]
    issues = audit_schedule(rows, START, START + timedelta(hours=1), Settings(_env_file=None))
    assert sum(v["code"] == "before_preparation" for v in issues) == 4
    assert any(v["code"] == "burst_window" for v in issues)


@pytest.mark.parametrize(
    "values",
    [
        {"pause_probability": 2},
        {"pause_lambda": 0},
        {"default_wpm_min": 0},
        {"business_hours_start": 22, "business_hours_end": 8},
        {"default_wpm_min": 70, "default_wpm_max": 40},
        {"duration_hours": float("nan")},
    ],
)
def test_bad_settings_or_campaign(values):
    with pytest.raises(ValidationError):
        if "duration_hours" in values:
            campaign(duration=values["duration_hours"])
        else:
            Settings(_env_file=None, **values)


def test_bad_message_and_count():
    with pytest.raises(ValidationError):
        Message(content="   ")
    with pytest.raises(ValidationError):
        Campaign(
            name="Synthetic",
            total_messages=2,
            duration_hours=1,
            messages=[Message(content="Synthetic event")],
        )


def test_naive_start_is_rejected():
    with pytest.raises(ValueError, match="timezone"):
        trace(1, start=START.replace(tzinfo=None))
