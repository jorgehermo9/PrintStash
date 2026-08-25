---
name: create-tests
description: Use when touching any test — creating a test file, adding a case to an existing file, editing or deleting a test, auditing whether a module is covered, or choosing which layer (pure / in-process / boundary / e2e / Playwright) a scenario belongs to. Carries the mandatory coverage matrix, the test-type policy, and the per-runtime conventions for pytest, vitest, and Playwright in this repo. Not for merely running tests — AGENTS.md has the commands.
---

# Create Tests

Any time you touch a test — a new file, a new case, an edited assertion, a
deletion — this skill applies. A one-line assertion change counts; there is no
"small tweak" carve-out.

Read this file in full, then the one reference for the runtime you're writing in:

| Writing in | Read |
| --- | --- |
| `backend/tests/**` (pytest) or `backend/packages/printstash-core/tests/**` | [references/backend.md](references/backend.md) |
| `frontend/src/**/__tests__/**` or `frontend/packages/*/src/__tests__/**` (vitest) | [references/frontend.md](references/frontend.md) |
| `frontend/tests/e2e-real/**` or `frontend/tests/e2e/**` (Playwright) | [references/playwright.md](references/playwright.md) |

## How much to test

Two independent questions decide a feature's tests:

1. **How much** — coverage completeness. *This section.*
2. **Which kind** — pure vs. in-process vs. boundary vs. e2e. *Test Type Policy, below.*

Pick the *type* per scenario; pick the *count* by covering every scenario. The
two don't trade off: **exhaustive** means every distinct behaviour has a test;
**not too many** means no redundant tests for the same behaviour and no
assertions on implementation details. One test per behaviour satisfies both.

### Default to exhaustive coverage

"Add a couple of tests" is not the bar. For every production change cover
**every happy path, every edge case, and every error path.** Sweep these
classes for each feature so none are silently skipped:

- **Boundaries** — min/max, off-by-one, size limits (`body_limit`, mesh
  limits, pagination `limit`/`offset`)
- **Empty / null / missing** — required field absent, optional omitted, empty
  collection, model with no files
- **Duplicates / idempotency** — same bytes uploaded twice (content-hash
  dedupe), delete-twice, replayed job, unique-constraint collision
- **Ordering / concurrency** — worker-thread writes racing reads, out-of-order
  provider events, GC running mid-operation
- **Auth / permission** — unauthenticated, wrong scope (`read`/`write`/`admin`),
  non-superuser, collection/printer RBAC role too low, share-link visibility
- **Live vs trashed** — every read over Models/Files respects
  `scopes.live()`; trashed rows are invisible, restorable, and GC'd on schedule
- **Malformed input** — wrong type, oversized, unparseable G-code/3MF, bad URL
  (SSRF guard), hostile filename
- **Downstream failure** — printer/provider times out, rejects a command,
  returns a malformed payload; notification target 5xx
- **Partial failure / rollback** — multi-step write fails midway (file row
  without metadata, thumbnail failing after persist); atomicity holds
- **Storage backend** — behaviour is identical through `StorageBackend`
  (local default; S3 branch when the change touches storage keys)
- **SQLite and PostgreSQL** — dialect-sensitive SQL (`tests/postgres/`) when
  the change adds a query, index, or migration

The fixtures make the Nth test nearly free — `db_session`, `client`,
`auth_headers`, the provider emulators, and `_patch_engine` truncating every
table between tests exist precisely so coverage is cheap. There is no budget to
ration.

### The coverage matrix — mandatory for every feature

Before writing tests for **any** feature, bug fix, or module — not only complex
ones — enumerate the behaviours as a coverage matrix. The matrix is how you
prove the exhaustive bar is met instead of asserting it.

**Rules:**

- **One row per observable behaviour**, which is also one test function / one
  `it` (see "One behaviour per test"). If a row's name needs the word "and",
  split it into two rows.
- **Derive rows from requirements, never from the implementation.** Reading
  the source and matrixing what it happens to do reproduces its bugs as
  "expected." Requirements live in the issue, `CONTEXT.md`,
  `docs/provider-support.md`, and the PR summary.
- **No blank cells.** Every row has a Status; a behaviour with no test is
  `❌ missing`, not omitted.

**Standard format** (a Markdown table — use these exact columns):

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Type | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| 1 | persists the file row and metadata together | Happy | staged STL, well-formed meta | `File` + `Metadata` rows exist; returned row has id | In-process | ✅ `test_ingestion_atomicity.py::test_persists_file_and_metadata_together` |
| 2 | dedupes a re-upload by content hash | Edge | same bytes uploaded twice | job state `duplicate`; one `Model` row | E2E | ✅ `test_e2e_ingest.py::test_gcode_upload_parses_metadata_and_dedups` |
| 3 | accepts a model name at the length limit | Edge | name = MAX chars | 201; row persisted untruncated | In-process | ❌ missing |
| 4 | hides a trashed model from the list | Edge | model with `deleted_at` set | `GET /models` omits it | In-process | ❌ missing |
| 5 | denies a `read`-scope token | Error | token scope `read` | 403; no row written | In-process | ❌ missing |
| 6 | returns all-None metadata for a file with no comments | Edge | G-code with no `;` lines | every metadata field is `None`; no exception | Pure | ✅ `test_gcode_parser.py::test_no_comments_returns_all_none` |
| 7 | surfaces a provider upload rejection | Error | emulator started with `reject_commands=True` | `ProviderError`; job state `failed` | Boundary | ❌ missing |

**What each column holds:**

- **Behaviour** — one observable behaviour phrased as the test name.
- **Category** — `Happy` / `Edge` / `Error`. Scanning this column shows at a
  glance whether all three were swept.
- **Precondition / input** — the arrange step: state and input driving this
  behaviour.
- **Observable outcome asserted** — the *real* artifact you assert on: a DB
  row, an HTTP status + body, a returned value, a file on the storage backend,
  a request the emulator's `Recorder` received. The boundary is the
  observable, not a spy on internal calls. Never "method X was called." If you
  can't name an observable outcome, the row isn't a behaviour — drop or
  rewrite it.
- **Type** — `Pure` / `In-process` / `Boundary` / `E2E` / `Playwright` /
  `Frontend unit`, chosen via the Test Type Policy below. Don't re-justify
  the choice in the cell; the policy is the source of truth.
- **Status** — exactly one of `✅ file::test_name` (covered), `❌ missing`
  (planned, not written), or `⏭️ N/A — <reason>` (deliberately not tested,
  reason inline).

**Surface the matrix** in your response and in the PR description (the PR
template has a section for it). An empty or `❌` cell is a visible missing
test, not a judgment call left to the reader.

### Close the loop: assess after every session

A test-writing session makes **two passes** over the matrix:

1. **Plan (before code).** Build the matrix from requirements with every
   Status `❌ missing`. This is your test plan, and it is the "tests first"
   step AGENTS.md rule 4 requires on data-integrity and security fixes.
2. **Assess (after writing).** Walk **every** row again and set its Status to
   `✅ <test>` or `⏭️ N/A — <reason>`. **Done = zero unexplained `❌`.**
   Re-print the completed matrix.

The closing assessment is mandatory. It converts "I added some tests" into
"every behaviour is covered or explicitly waived." Skipping it forfeits the
guarantee the matrix exists to provide.

### Assessing an existing suite

To answer "are the tests already here enough?" (an audit, not fresh
authoring): build the same matrix from requirements, then populate Status by
reading the **current** suite — `✅` where a test already covers the row,
`❌ missing` where none does. The `❌` rows are the coverage gap; report them
as the deliverable.

## Test Type Policy

**"Write tests. Not too many. Mostly integration."** The highest
confidence-per-test comes from tests that wire real components together.
Agents drift toward mocked unit tests because they're easier to generate; this
section exists to counteract that.

In this repo the real database is *free*: every test that takes `db_session`
or `client` runs against a real SQLite engine with the production pragmas
(`foreign_keys=ON`), real routers, real services, and a table wipe between
tests. So the default test type is **in-process**, and the only things you
ever stand in for are egress boundaries.

### The layers

| Type | Where | What is real | What is stood in for | Lane |
| --- | --- | --- | --- | --- |
| **Pure** | `backend/tests/test_<concern>.py`, `printstash-core/tests/` | the function | nothing | `fast` |
| **In-process** *(default)* | `backend/tests/test_<concern>.py` | SQLite, routers, services, storage backend, RBAC | outbound HTTP (`get_http_client`), provider clients | `fast` |
| **Boundary** *(`integration` marker)* | `backend/tests/test_<provider>_integration.py`, `test_real_*.py`, `tests/postgres/`, `test_migrations.py`, `test_storage_s3.py` | a loopback emulator over a real socket, a real slicer file, PostgreSQL, SeaweedFS, Alembic | nothing | `integration` |
| **E2E** *(`e2e` marker)* | `backend/tests/e2e/` | the whole app via `httpx.ASGITransport` + contract-enforcing fakes | nothing (`is_public_ip` relaxed for loopback) | `e2e` |
| **Frontend unit** | `frontend/src/**/__tests__/` | component/hook + real collaborators (query hooks, api client, router, auth context) | `fetch` via `vi.stubGlobal`, or `QueryApiProvider` stubs | `pnpm test` |
| **Playwright real** | `frontend/tests/e2e-real/` | browser + Vite + real uvicorn + throwaway SQLite (+ mock printer) | nothing | `pnpm test:e2e:real` |
| **Playwright mock-API** | `frontend/tests/e2e/` | browser + Vite | the API (`mock-api.ts`) | `pnpm test:e2e` |

The `integration`/`e2e` markers are applied by filename convention in
`backend/tests/conftest.py::pytest_collection_modifyitems` — name the file
correctly and it lands in the right lane.

### When in-process (or deeper) tests are MANDATORY

- **Every router endpoint** — the full request→response cycle through
  `TestClient`: auth scope, RBAC, validation, response shape, DB side effect.
  Only egress is mocked.
- **Every service that writes the DB** — `ingestion.persist_artifact`,
  `trash`, `library_transfer`, `printer_jobs`, `backup`: transactions,
  multi-table writes, cascades, constraint violations. These surface only
  against a real engine.
- **Every query over a soft-deletable table** — a test that a trashed row is
  invisible through the read path (`scopes.live()`), and visible through
  `scopes.trashed()`.
- **Every migration** — `test_migrations.py` upgrade path; add a
  `tests/postgres/` case when the SQL is dialect-sensitive.
- **Every provider change** — the shared conformance pack
  (`test_provider_conformance.py`) picks it up automatically; behaviour goes in
  `test_<provider>.py`, and wire-level behaviour in
  `test_<provider>_integration.py` against its emulator.
- **Every new feature** — AGENTS.md rule: *unit tests + one e2e test for its
  headline capability* (backend `tests/e2e/`; for UI features also
  `frontend/tests/e2e-real/`).

### When pure / mocked tests are the right choice

- **Pure logic** — parsers (`gcode_parser`, `bgcode`), hashing, URL safety,
  slug/taxonomy helpers, `model_views` mapping given built rows, frontend
  `lib/` formatters.
- **Faults hard to reproduce for real** — network timeout, malformed provider
  JSON, rate limit, a dependency raising on cue. Patch `get_http_client` (or
  inject a fake client/factory) and assert the reaction.
- **Complex branching** — state machines (`PrintSim`, job state transitions),
  many input combinations.
- **Frontend components and hooks** — rendering, interaction, store behaviour.

### Decision matrix

| What you're testing | Type | Stand-in strategy |
| --- | --- | --- |
| Router endpoint | **In-process** | `client` + `auth_headers`; mock egress only |
| Service with DB writes | **In-process** | `db_session`; real storage backend |
| Live/trashed visibility, RBAC resolution | **In-process** | real rows, real roles |
| Dialect-sensitive SQL, migration | **Boundary** | `tests/postgres/` with `PRINTSTASH_TEST_POSTGRES_URL` |
| Provider wire protocol | **Boundary** | emulator over loopback (`start_server`, `PrintSim`, `Recorder`) |
| Real slicer output | **Boundary** | fixture under `tests/fixtures/` (`test_real_*`) |
| Headline flow of a feature | **E2E** | `api` + `fakes` fixtures in `tests/e2e/` |
| Pure function | Pure | none |
| Reaction to a dependency failing | Pure/In-process | `patch("<module>.get_http_client")`, injected factory |
| React component / hook | Frontend unit | `vi.stubGlobal("fetch")`, `QueryApiProvider`, seeded `QueryClient` |
| UI flow with persistence | Playwright real | none |
| Route renders without console errors | Playwright mock-API | `mock-api.ts` |

### Never mock inside a boundary or e2e test — induce only real faults

A boundary or e2e test exercises real wiring end to end. The moment you
`patch`, `monkeypatch`, or override a seam *inside* one to force a failure, it
stops being a boundary test on that path — it's a unit test in an integration
costume, and it proves nothing about the real system.

The fault you want decides the type:

- **Reachable against the real fake, deterministically → boundary test, real
  fault.** The emulators take fault flags for exactly this:
  `reject_commands=True`, a wrong `expected_access_code`, `PrintSim` driven
  to `ERROR`, the `/flaky/{key}` webhook target, `--auth-mode` on PrusaLink.
  Add a flag to the fake when the fault you need isn't there yet.
- **A dependency misbehaving on cue (raises once, returns garbage, times out
  N times then succeeds) → in-process/pure test, patched boundary.** "How does
  our code react when the client raises?" is logic over the dependency's
  *outcome*, not a property of real infra.

The smell test: **if making the boundary test fail requires replacing part of
the real system with a fake, that assertion belongs in an in-process test.**
The e2e conftest's single monkeypatch (`is_public_ip` for loopback) is the
ceiling, not a precedent.

### One behaviour per test

Each test asserts on **one observable behaviour**, and its name says exactly
which. If the natural name needs the word "and", split.

```python
# ❌ two behaviours in one test
def test_create_printer_returns_201_and_persists_row(): ...

# ✅ one behaviour per test
def test_create_printer_returns_the_created_printer(): ...
def test_create_printer_persists_a_row(): ...
def test_create_printer_rejects_read_scope(): ...
```

Why: the failing test's name tells you which behaviour broke; each test is
independently skippable; setup is paid per fixture, not per test, so splitting
costs nothing. Applies equally to in-process, boundary, e2e, and Playwright
specs — the Playwright real suite's long lifecycle specs are the one deliberate
exception, because each one *is* a single headline flow.

Common conflations: "captures X and Y" → one test per dimension; "returns 200
and writes to DB" → response shape vs. side effect; "handles success and
failure" → always split; "sets header and forwards body" → one per output
channel.

### Anti-patterns

- **Superficial integration** — a `_integration.py` or e2e test that boots
  the emulator, then patches the provider client. Either drive the real fake
  or move to an in-process test.
- **Mocking the DB** — never. `db_session` is real and cheap. A `MagicMock`
  session tests nothing about `scopes`, cascades, or constraints.
- **Asserting on mock call arguments as the outcome** —
  `mock.assert_called_with(...)` tests wiring. Assert the row, the response,
  the storage object, the `Recorder` entry. (Asserting the *request the
  emulator received* is fine — the boundary is the observable.)
- **Mirroring the implementation** — tests derived from reading the source
  pass by construction. Derive from requirements.
- **Hand-written `deleted_at.is_(None)` in test queries** — use
  `scopes.live()` / `scopes.trashed()` in tests too; a hand-rolled predicate
  drifts from the production one.
- **Mocking static registries** — `PROVIDERS`, `Capability`, `FileType`,
  `queryKeys`. Mock the *service* that reads them, never the constants.
- **Order-dependent tests** — `pytest-randomly` shuffles and `xdist`
  parallelises. A test that passes only after another ran has shared mutable
  state: a module-level singleton not reset in `_patch_engine`, an
  `_overlay` key set without cleanup, a file written outside `tmp_path`. Fix
  the state, not the order.
- **Cross-test collisions on the shared Playwright DB** — the real suite runs
  serially on one DB and only wipes it per *launch*. Any name a spec writes
  must be per-run unique (`` `e2e-model-${Date.now()}` ``) and the spec cleans up
  what it created.
- **Implicit "does not raise"** — when not-raising *is* the contract (best-
  effort cleanup, GC that must not propagate), assert it explicitly: the
  return value, the unchanged row, `assert caplog` empty of errors. A bare
  call that happens not to raise is an accidental assertion with a confusing
  failure message.
- **Real secrets or access codes in fixtures** — never. Use obviously fake
  values (`"12345678"`, `"key"`).
- **Skipping the changelog/contract fallout** — a response-shape change
  updates `tests/fixtures/openapi_contract.json`
  (`UPDATE_OPENAPI_CONTRACT=1`) and, for provider contracts,
  `frontend/src/generated/printer-contracts.ts` via the codegen `--check`.
  Regenerating without reading the diff hides an accidental API break.

## Test data

- **Round numbers** (100, 50, 25) for calculations; **absolute instants**
  (`"2026-01-01T00:00:00Z"`) instead of `now() ± offset`.
- **Build rows through the models** (`Model(...)`, `Printer(...)`,
  `User(...)`) with module-local `_make_*` helpers; the repo has no factory
  library and doesn't need one. Helpers create-or-fail — never
  create-or-reuse, which hides collisions.
- **Real slicer files** live in `backend/tests/fixtures/` (Orca, Prusa, Cura,
  Bambu Studio, real MK4/Ender-3 outputs). Reach for one before hand-writing
  G-code; hand-write only when the header under test must be minimal.
- **Unique bytes per model** where content-hash dedupe applies — embed the
  model name in the G-code/STL so two uploads stay two models.

## What NOT to test

Simple getters, third-party behaviour, generated code (Alembic scaffolding,
`printer-contracts.ts`, `types/` from OpenAPI), and components that only
render data without logic.

## Validate before you report

Backend: `cd backend && ./scripts/test.sh fast -q` for the loop, `./scripts/test.sh
full -q` before claiming green (coverage gate is `--cov-fail-under=95` in
CI); `uv run ruff check app/ tests/`; `uv run pyright`. Frontend: `pnpm lint
&& pnpm typecheck && pnpm test`. Report the exact result — never say tests
passed without running them, and paste failures verbatim.
