"""Seeded synthetic offline schedule export. No backend or provider imports."""

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import numpy as np

from tempoledger.config import Settings
from tempoledger.models import Campaign, Message
from tempoledger.scheduling.audit import audit_schedule
from tempoledger.scheduling.engine import JitterScheduler


def replay(seed=7, count=12, duration_hours=2.0, start_hour=9):
    config = Settings(_env_file=None)
    start = datetime(2030, 1, 7, start_hour, tzinfo=UTC)
    campaign = Campaign(
        id=UUID(int=1),
        name="Synthetic maintenance events",
        total_messages=count,
        duration_hours=duration_hours,
        created_at=start,
        messages=[
            Message(id=UUID(int=i + 10), content=f"Synthetic event {i}: inspect fixture queue")
            for i in range(count)
        ],
    )
    rows = JitterScheduler(config, seed=seed).schedule_campaign(campaign, start)
    intervals = np.array(
        [(b.send_time - a.send_time).total_seconds() for a, b in zip(rows, rows[1:])]
    )
    mean = float(intervals.mean()) if len(intervals) else 0.0
    stats = {
        "count": len(rows),
        "pause_count": sum(r.reasoning["had_pause"] for r in rows),
        "span_seconds": (rows[-1].send_time - rows[0].send_time).total_seconds(),
        "mean_interval_seconds": mean,
        "interval_cv": float(intervals.std() / mean) if mean else 0.0,
        "mean_wpm": float(np.mean([r.reasoning["wpm_sampled"] for r in rows])),
    }
    parameters = {
        key: getattr(config, key)
        for key in [
            "default_wpm_mean",
            "default_wpm_std",
            "default_wpm_min",
            "default_wpm_max",
            "pause_probability",
            "pause_lambda",
            "business_hours_start",
            "business_hours_end",
            "min_interval_variance",
            "min_coefficient_of_variation",
            "max_burst_messages",
            "burst_window_seconds",
        ]
    }
    return {
        "schema_version": 1,
        "algorithm": "inherited-heuristic-isolated-rng-v1",
        "data": "authored-synthetic",
        "delivery": "not-executed",
        "seed": seed,
        "numpy_version": np.__version__,
        "start": start.isoformat(),
        "duration_hours": duration_hours,
        "parameters": parameters,
        "statistics": stats,
        "schedule": [r.model_dump(mode="json") for r in rows],
        "violations": audit_schedule(rows, start, start + timedelta(hours=duration_hours), config),
    }


def main():
    parser = argparse.ArgumentParser(description="Export a synthetic offline workload schedule")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--count", type=int, default=12)
    parser.add_argument("--duration-hours", type=float, default=2.0)
    parser.add_argument("--start-hour", type=int, default=9, choices=range(24))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = replay(args.seed, args.count, args.duration_hours, args.start_hour)
    text = json.dumps(result, indent=2, allow_nan=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)


if __name__ == "__main__":
    main()
