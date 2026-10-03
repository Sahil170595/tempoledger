"""Independent checks expose conflicts that the inherited heuristic does not resolve."""

from datetime import datetime, time, timedelta

from tempoledger.config import Settings
from tempoledger.models import ScheduledMessage


def audit_schedule(
    rows: list[ScheduledMessage], start: datetime, end: datetime, config: Settings
) -> list[dict]:
    issues = []
    opening = time(config.business_hours_start)
    closing = time(config.business_hours_end)
    previous = None
    for index, row in enumerate(rows):

        def record(code, detail):
            issues.append(
                {"index": index, "message_id": str(row.message.id), "code": code, "detail": detail}
            )

        if not start <= row.send_time <= end:
            record("outside_campaign", "Scheduled time is outside the campaign horizon")
        if not opening <= row.send_time.time() <= closing:
            record("outside_business_hours", "Scheduled time is outside configured opening hours")
        if previous and row.send_time < previous:
            record("out_of_order", "Scheduled time precedes the previous event")
        prepared_from = row.reasoning.get("prepared_from")
        if prepared_from:
            ready = datetime.fromisoformat(prepared_from) + timedelta(
                seconds=row.reasoning.get("typing_duration", 0)
            )
            if row.send_time < ready:
                record(
                    "before_preparation", "Scheduled time precedes sampled preparation completion"
                )
        lower = row.send_time - timedelta(seconds=config.burst_window_seconds)
        in_window = sum(lower < r.send_time <= row.send_time for r in rows[: index + 1])
        if in_window > config.max_burst_messages:
            record("burst_window", f"{in_window} events in the preceding window")
        previous = row.send_time
    return issues
