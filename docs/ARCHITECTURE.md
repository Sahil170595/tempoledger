# Architecture And Failure Cases

## Ownership Boundaries

`models/` defines in-memory contracts. `scheduling/engine.py` samples timing and
applies a campaign-level projection. `scheduling/audit.py` independently checks
the output. `replay.py` is an offline application; it has no backend dependency
imports, provider invocation or persistence side effects unless an output path
is explicitly supplied.

`api/routes/campaigns.py` validates requests, schedules work and inserts message
rows. `services/database.py` owns pooled parameterized SQL and schema fallback.
The Alembic migration is the preferred schema definition. The event table is
available for explicit logging but is not a controller event bus.

`workers/tasks.py` contains a due-row dispatcher and send task. `services/sms.py`
separates mock progression from the optional provider adapter. `agent/controller.py`
generates proposals, never commands that mutate the queue. Telemetry is lazy;
without an exporter endpoint the OpenTelemetry API records via its default
no-op provider rather than creating a remote export stream.

## Failure Catalog

| Boundary | Concrete failure | Observable evidence / remaining work |
| --- | --- | --- |
| Preparation -> cluster jitter | A negative normal perturbation can precede preparation completion | `before_preparation` audit; use lower-bound-aware projection |
| Distribution -> campaign clamp | Relative offsets containing clock hours hit short-campaign endpoints | Repeated end timestamps; distinguish clock windows from relative delays |
| Horizon -> opening window | A wholly after-hours horizon has no feasible opening interval | `outside_business_hours`; fail or explicitly expand the horizon |
| Burst heuristic -> hard bound | A 30-60 second delay need not clear a 90-second dense window | `burst_window`; compute earliest feasible slot |
| API -> storage | Independent inserts can partially persist a campaign on failure | Introduce transaction plus durable campaign metadata |
| Poller -> broker | Two polls can read the same unclaimed rows before a send finishes | Atomic claim / outbox / idempotency key needed |
| Worker -> asyncpg | Pool created on one event loop may be used on a different loop | Needs worker-owned lifecycle qualification with real services |
| Mock task -> shutdown | Background status progression can be cancelled by `asyncio.run` ending | Treat status as simulated; never infer carrier confirmation |
| Proposal -> mutation | Tools only write logs | `implemented: false`; no actual pause/reschedule implementation |
| Telemetry -> score | Pending rows lower delivered fraction, yet contribute to score baseline | Diagnostic formula is not a success probability |
| Health -> readiness | Provider credentials are checked, not a provider round trip | Readiness does not establish delivery readiness |
| Client -> public API | No authentication, quota/recipient policy or tenant boundary | Bind loopback; do not deploy as a public service |

Opening hours are daily fixed clock bounds, not a timezone/calendar engine.
Closing time is inclusive in this implementation; no weekend, holiday or DST
exceptions exist. Input datetimes must be timezone-aware. The replay uses UTC.

The interval helper scans accumulated history rather than maintaining rolling
statistics, so campaign cost grows with history. The inspected workloads of
tens of messages are not scalability evidence. No latency percentile is asserted
by this release and no real provider delivery metric is imported.

## What Changed For This Release

The timing distributions and inherited projection order are retained. RNG state
is per scheduler instead of process-global, so independent replays do not alter
one another's seed stream. Validation now rejects nonfinite settings, contradictory
WPM/opening ranges, whitespace-only text and mismatched campaign counts.

Provider calls require explicit opt-in beyond credentials. The due-row dispatcher
rejects live mode because no recipient mapping exists; its synthetic destination
is only for mock execution. Tool returns no longer claim success for a log entry.
Names/prompts describe workload scheduling and operational flags, not a behavioral
claim. Raw original prose and examples are replaced, not published.

Worker delivery metadata is JSON-encoded for asyncpg JSONB parameters. Alembic's
SQLAlchemy async URL retains its async driver. The alert enum lookup is corrected.
Circuit-breaker helpers are preserved but are not represented as active protections.

## Evidence Ladder

The test suite exercises actual timing computation, a reproducible fixture,
in-process FastAPI with a fake store, Celery task bodies with fake boundaries,
mock gateway progression, fallback proposal routing, rate limits, retries and
telemetry calls. Tests block outbound sockets; on Windows only asyncio's internal
socketpair construction is permitted. No model/provider request is needed.

These tests do not qualify broker delivery, Postgres transactions/migrations,
provider webhooks, restart recovery or distributed concurrency. Hosted CI runs
the same offline tests and emits migration SQL without connecting to a database.
It does not close those infrastructure gaps. No original benchmark or operational
claim is carried into the synthetic statistics.
