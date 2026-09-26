# Run background work level-triggered on a durable engine

Background work used to be spread across mechanisms that could each lose it:
FastAPI `BackgroundTasks`, a hand-rolled job registry, eight lifespan loops, an
in-memory wakeup, in-process maintenance fences, and enrichment (metadata,
thumbnails, fingerprints) done inside the upload request. A crash, a restart or
a second process dropped work on the floor, and nothing noticed.

All of it now runs as Jobs on one engine behind a port
(`app/modules/work`), executed by DBOS (`app/runtime/engine`). The rules below
are what keep that correct; `docs/architecture/background-work.md` describes the
mechanics.

**The application database records intent; the engine only executes.** Intent
is typed domain data that evolves under Alembic: a Job row, an ingest request,
a missing derivative, a due schedule. A Job's input is never serialized into
the engine and replayed, so an upgrade never meets a payload it cannot read,
and the engine's own state is disposable: a restore resets it and the
reconciler rebuilds from the restored vault.

**Work is level-triggered.** Each definition's Work Source computes pending
work from domain state, and a reconciler submits it. Hot paths only nudge the
reconciler to run a pass sooner; a lost nudge costs latency, never the work. A
pass is bounded by its batch and its lane's headroom, continues itself while a
full batch leaves room, and re-runs when a nudge arrived during it (the dirty
mark). Edge-triggered submission, where a request enqueues the job directly,
was rejected: it is exactly what lost work before.

**Two engine guarantees, named in the port.** `execution_id`
(`<job>:<attempt>`) runs at most once per attempt, and `dedupe_key`
(`<definition>|<subject>`) keeps at most one active execution per subject.
Both map to DBOS primitives (workflow ID and queue deduplication) rather than
being reimplemented. Partitioned lanes (notifications per channel, printing per
printer) cannot deduplicate in DBOS, so the active-subject index on `jobs` is
the claim there.

**Enrichment is a derivative, pulled.** An upload commits a bare Artifact and
knows nothing about derivative kinds. A bounded anti-join finds Artifacts
missing a kind at its current recipe version, so a new kind, a recipe bump or
"regenerate all" needs no backfill code and no startup `UPDATE`. A missing
derivative is unknown, never zero: search, filters and similarity treat it so.

**Everything is bounded.** Passes by batch and headroom, steps by their retry
policy, resubmits by `jobs_max_resubmits` and a cooldown, derivative attempts by
an exponential backoff and a maximum, schedules by supersession (a missed
occurrence is not replayed once per interval missed).

**Cancel withdraws intent.** Each definition declares what cancelling does to
its subject before the engine is told: a staged upload is released, a
derivative is marked cancelled, a scan is abandoned. Otherwise the reconciler
would find the intent and resubmit it. There is no pause or resume; a paused
Job is indistinguishable from a stuck one to every other process.

**Only the application version counts.** A process of a new version cancels
executions another version left running and the reconciler reruns them on the
new code. Per-job versioning was rejected: it multiplies the code paths that
must stay replayable, for no benefit once inputs are never replayed.

**The engine stays behind the port.** DBOS is imported only under
`app/runtime/engine/`; modules declare definitions and steps with our
contracts, and tests run the same contract against the inline engine and DBOS.
`printstash-core` stays engine-free. Alternatives rejected: Redis-backed queues
(a new hard dependency for a local-first product), a bespoke claim table (the
thing being replaced), and Celery-style workers (no durable step state).

Topologies follow from the same rules: one process running everything (the
default, SQLite or PostgreSQL), an API that runs its own jobs next to workers,
or an API that runs none with `python -m app.worker` replicas. A split topology
requires PostgreSQL and a volume every process mounts (uploads are staged on
local disk, and the worker committing one reads what the API staged); the API
owns the schema and workers wait for it. Realtime notices carry ids only and cross
processes over PostgreSQL `NOTIFY`; clients refetch through authorized
endpoints.
