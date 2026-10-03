# Tempoledger

Seeded workload/event scheduling with inspectable timing decisions and a full
asynchronous backend. The offline path computes real schedules, exports replayable
JSON and reports constraint violations. Delivery in examples is not executed;
the backend defaults to a simulated gateway. This is an engineering reference,
not a validated behavioral model or a production delivery service.

Portfolio: [work collection](https://chimeraforge.vercel.app/work).
The [browser demo](https://chimeraforge.vercel.app/projects/systems/send-pacing) ports
`scheduling/engine.py`, `audit.py` and `replay.py` to TypeScript with a bit-exact port of
NumPy's legacy `RandomState`, reproduces this repository's replays to the microsecond, and
runs the audit over 1,000 seeds. It does not include the API, database, workers or providers.

## Offline Reproduction

Python 3.11 or newer. The core requires NumPy, Pydantic and pydantic-settings.
Use an existing compatible environment or prepare a separate environment:

```sh
python -m pip install -e '.[backend,dev]'
python -B -m tempoledger.replay --seed 7 --output output/seed7.json
python -B -m tempoledger.replay --seed 8 --output output/seed8.json
python -B -m tempoledger.replay --seed 7 --count 5 --start-hour 20 --duration-hours 1
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -B -m pytest tests -q -p no:cacheprovider
ruff check --no-cache tempoledger tests
ruff format --check --no-cache tempoledger tests
python -B scripts/check_release.py --ref HEAD
```

On PowerShell set `$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'` before pytest instead
of using the shell prefix. No external service, provider credential or model is
needed for these checks. Backend tests use in-process HTTP and fake database/
gateway boundaries; they are not a PostgreSQL/Celery integration qualification.

Each replay includes the seed, algorithm/schema version, NumPy version, timing
parameters, authored synthetic messages, every sampled timing reason and an
independent audit. UUIDs and start timestamps in the fixture are deterministic.
Recreate the scheduler with the same seed to reset it: one instance advances its
own RNG between calls. JSON export excludes service settings and secrets.

## Underlying Implementation

The retained system is more than an interval generator. It includes Pydantic
message/campaign/event/session models, a NumPy timing engine, FastAPI campaign
and telemetry routes, PostgreSQL access and migrations, Celery queue/send tasks,
optional provider adapters, structured logging, retries, circuit-breaker helpers,
OpenTelemetry instrumentation and logging alert channels.

### Timing Pipeline

`tempoledger/scheduling/engine.py` implements this sequence:

1. Sample typing rate from a normal distribution (mean 50, standard deviation 15),
   clipped to 30-80 words per minute. Preparation time uses whitespace word count.
2. Draw a pause with probability 0.4; its exponential duration uses rate 0.08,
   clipped to 5-45 seconds. `typing_duration` includes that pause.
3. Near configured peak-hour quarter-hour opportunities, perturb the preparation
   target by a zero-mean normal draw with 180-second standard deviation. This
   can move a target backward; it is not a feasibility-preserving projection.
4. Inspect historical intervals: low variance adds gamma noise, a repeated
   interval gets normal perturbation, and a dense recent window gets a bounded
   random delay. These are heuristics, not hard spacing guarantees.
5. For a single message, project outside-hours targets forward to opening time.
   Campaign scheduling also applies its distribution plan and then clamps into
   the campaign horizon, attempting to satisfy opening hours within that horizon.

The campaign distribution allocates integer-rounded 30% peak, 50% uniform and
the remainder quarter-hour proposals. An important inherited mismatch remains:
the peak/quarter-hour values are generated like clock-hour offsets but later
added relative to campaign start. Short campaigns therefore fall back to uniform
draws or accumulate clipped proposals at their endpoint. Sorted final timestamps
alone do not establish that preparation, burst or opening-hour constraints held.

### Backend Data Flow

```text
Campaign request -> validated models -> timing engine
                                      -> scheduled_messages in PostgreSQL

Manual queue poll -> due rows (limit 10) -> Celery send task
                                       -> simulated gateway by default
                                       -> status/JSONB update + telemetry

Synthetic lifecycle event -> rules or opt-in external decision generation
                         -> tool proposal -> log only; no scheduling mutation
```

PostgreSQL keeps UUIDs, content, timezone-aware planned/actual timestamps,
statuses and JSONB reasoning. Composite indexes serve per-session schedules
and due/status queries. A separate events table exists, but the controller does
not automatically populate it. The database wrapper uses an asyncpg pool
(minimum 5, maximum 20 connections) and retry decorators.

FastAPI exposes `POST /api/v1/campaigns`, `GET /api/v1/campaigns`,
`GET /api/v1/campaigns/{id}`, health endpoints and
`GET /api/v1/telemetry/health/{id}`. Campaign retrieval is reconstructed from
message rows, not from a persisted campaign object. Inserts occur individually,
not in a campaign-wide transaction. Rate limiting defaults to process-local
memory; set `RATE_LIMIT_STORAGE_URI` to a Redis URI for shared limits.

Celery tasks select due messages and submit send work; queue claiming and send
idempotency are absent. The queue dispatcher has no recipient mapping and is
**simulation-only**, rejecting live mode rather than inventing a destination.
Beat has no configured recurring poll: dispatch explicitly or implement a
deployment-specific schedule. Retry/backoff code is retained; circuit-breaker
helpers exist but are not applied to database or gateway operations.

### Agent Boundary

The rules route operator flags to pause/alert proposals, pattern anomalies to a
parameter proposal and replies to a buffer proposal. The optional external model
adapter builds tool schemas and parses tool calls; it does not train a model.
The injected memory interface can record event/proposal history, but no concrete
Redis session-memory implementation is included.

**`adjust_schedule`, `reschedule_queue`, `pause_session` and `alert_operator`
only log.** They do not update scheduler parameters, mutate queues, stop workers
or notify a person. Every tool result says `implemented: false`, `success: false`
and `logged: true`. A tool-call trace must not be read as evidence of execution.

## Findings And Evidence Boundaries

The original implementation's tests exercised individual schedules and campaigns
of tens of messages, including a 50-message workload. They do not establish an
operational throughput limit, latency percentile, real delivery rate or behavioral
validity. This release does not carry over numeric success claims from those tests.

Fresh authored fixture, Python 3.13.1 / NumPy 2.2.6, seed 7, 12 events over two
hours starting 2030-01-07 09:00 UTC:

| Quantity | Recomputed result |
| --- | ---: |
| Events | 12 |
| Sampled pauses | 7 |
| First-to-last span | 7085.445401 seconds |
| Mean interval | 644.131400 seconds |
| Interval coefficient of variation | 1.435039 |
| Mean sampled typing rate | 52.824277 WPM |
| Preparation-time audit violations | 2 |

`examples/seed7-summary.json` records these results; tests reproduce them within
floating-point tolerance. These are synthetic fixture statistics, not original
benchmark results or evidence of generalization. The violations are a finding,
not a passing feasibility score. Higher CV is not inherently a better schedule.

The after-hours fixture (20:00 start, one-hour horizon) reports outside-hours
events because the campaign horizon and opening window do not intersect.
`audit_schedule` also checks horizon bounds, ordering, preparation completion
and sliding-window event counts. It observes the output; it does not repair it.

The telemetry route computes interval variance/CV, delivered/failed fractions
over **all scheduled rows**, and a score `0.7*delivery_rate + 0.3*(1-bounce_rate)`.
Pending rows affect the denominator, and an entirely pending session scores 0.3.
This is an operational diagnostic, not a delivery-success probability. Entropy,
activity adherence and other session-model fields are not computed by that route.

## Tradeoffs And Limits

The custom timing logic is easy to inspect and replay, while commodity API, queue,
database and telemetry libraries handle infrastructure. JSONB accommodates changing
reasoning schemas without a migration per parameter, but makes validation and
historical consistency the application's responsibility. UUID traces help correlate
events; high-cardinality telemetry attributes may be expensive at real scale.

The main engineering lesson is that a sampled distribution, a final constraint
projection and a persisted queue are separate contracts. Clamping one does not
preserve the others. Feasibility requires a proper constraint solver or explicit
infeasibility handling, not merely sorting timestamps. See
[architecture and failure cases](docs/ARCHITECTURE.md) for the retained boundaries.

Other limits: no authentication/authorization, no delivery webhook receiver,
no consent/recipient registry, no transactional campaign insertion, no distributed
queue claims, no idempotent sends, and unqualified cross-event-loop database-pool
lifetime in Celery. Opening hours use the input datetime's timezone; there is no
holiday/weekend/DST calendar. The mock's delayed status update is in-memory and
may be cancelled when a short-lived event loop ends. It is never real delivery.
Do not expose the API publicly or use the workers as a real delivery service.

## Optional Backend Exploration

With independently provisioned PostgreSQL and Redis services, configure your own
URLs in the shell, apply migrations and run the API bound to loopback:

```sh
export DATABASE_URL='postgresql://localhost:5432/tempoledger'
export REDIS_URL='redis://localhost:6379/0'
python -m alembic upgrade head
uvicorn tempoledger.api.main:app --host 127.0.0.1 --port 8000
```

The API lifespan also attempts migrations. Readiness treats database health as
critical, reports Redis separately and only checks provider configuration, not
actual delivery availability. Service orchestration is not verified by the offline
tests. No container images are required by this repository's offline workflow.

Provider adapters are retained for source completeness and disabled by default.
Credentials alone do not enable them: separate `ENABLE_LIVE_DELIVERY` and
`ENABLE_LIVE_AGENT` opt-ins are required. Optional SDKs are in the `providers`
extra. Do not enable live mode merely to reproduce this repository's results.
Dotenv files are not loaded automatically. `.env.example` contains only neutral
configuration examples, not credentials.

## Release Scope And License

This is a fresh, history-free source export with neutral module names, newly
written documentation and synthetic examples/tests. It retains the meaningful
scheduler/backend source, not private deployment records or datasets. The browser
demo runs the scheduler, audit and replay, but not the Python API, database,
workers, external providers or agent execution.

Released under the MIT license; see [LICENSE](LICENSE). [NOTICE](NOTICE.md)
lists the changes made for this release.
Dependency licenses remain with their respective projects; dependencies are not
vendored here.
