"""Stable synthetic output, failure cases and secret-free replay schema."""

import json
from collections import Counter
from pathlib import Path

import pytest

from tempoledger.replay import replay


def test_replay_reset_and_export():
    a = replay()
    assert a == replay()
    assert a != replay(seed=8)
    assert json.loads(json.dumps(a, allow_nan=False)) == a
    assert a["delivery"] == "not-executed" and a["data"] == "authored-synthetic"
    assert not any("key" in k or "token" in k or "url" in k for k in a["parameters"])


def test_fixture_conflicts_remain_observable():
    result = replay(count=5, start_hour=20, duration_hours=1)
    assert any(v["code"] == "outside_business_hours" for v in result["violations"])
    assert result["statistics"]["count"] == 5


def test_committed_fixture_statistics_recomputed():
    expected = json.loads((Path(__file__).parents[1] / "examples/seed7-summary.json").read_text())
    result = replay(seed=expected["seed"])
    assert result["statistics"] == pytest.approx(expected["statistics"], rel=1e-7)
    assert dict(Counter(v["code"] for v in result["violations"])) == expected["violation_counts"]
