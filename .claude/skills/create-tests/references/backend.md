# Backend tests (pytest)

Conventions for `backend/tests/**` and `backend/packages/printstash-core/tests/**`.
The policy (matrix, type selection, one behaviour per test) is in
[SKILL.md](../SKILL.md); this file is the runtime-specific how.

## Where a test lives

| Subject | File | Marker (auto, by filename) |
| --- | --- | --- |
| A concern's pure + in-process behaviour | `tests/test_<concern>.py` | — (`fast` lane) |
| A provider's normalisation/logic | `tests/test_<provider>.py` | — |
| A provider over its emulator (real socket) | `tests/test_<provider>_integration.py` | `integration` |
| Real slicer/mesh files | `tests/test_real_<subject>.py`, `*_realfiles.py` | `integration` |
| PostgreSQL dialect + concurrency contracts | `tests/postgres/test_*.py` | `integration` |
| Alembic upgrade path | `tests/test_migrations.py` | `integration` |
| S3 backend / backup destination | `tests/test_storage_s3.py` | `integration` (runs in the `storage-s3` CI job) |
| Headline flow through the real app | `tests/e2e/test_e2e_<flow>.py` + `pytestmark = pytest.mark.e2e` | `e2e` |
| Framework-neutral contracts | `packages/printstash-core/tests/test_<module>.py` | own pytest; may not import `app` (`test_forbidden_imports.py`) |

Markers come from `tests/conftest.py::pytest_collection_modifyitems`. Name the
file per the table and the lane is right; `./scripts/test.sh
{fast|integration|e2e|full}` selects on those markers.

## File shape

- Module docstring states the **contract** the file defends, in prose, and
  why it matters (see `test_ingestion_atomicity.py`,
  `test_provider_conformance.py`). A reader should know what breaks when the
  file goes red without opening the source.
- Group by unit: one `class Test<Endpoint|Method>` per endpoint or method
  (`TestCreatePrinter`, `TestPrinterRbac`), or flat `test_*` functions when the
  file covers one function. Never invent ad-hoc groups like `TestExtraCases` —
  a new aspect of an existing method is a sibling test in that class.
- Test names read as behaviours: `test_rejects_read_scope`,
  `test_hides_trashed_models_from_list`. No `test_1`, no `test_works`.
- Module-local `_make_*` / `_user_headers(...)` helpers build rows through the
  SQLModel classes; keep them create-or-fail. No factory library.

## Fixtures you get for free (`tests/conftest.py`)

| Fixture | Gives you | Use for |
| --- | --- | --- |
| `db_session` | a `Session` on the shared in-memory SQLite engine (production pragmas, `foreign_keys=ON`) | every in-process service/query test |
| `client` | `TestClient(app)` with hub, provider registry, `LocalTaskQueue` attached | every router test |
| `auth_headers` | bearer header for a fresh superuser with `admin` scope | admin happy paths; write `_user_headers(db_session, name, is_superuser=False, scope="write")` for RBAC rows |
| `app`, `hub` | the FastAPI app / a `PrinterHub` on `InProcessBus` | direct hub/service driving |
| `threaded_hub_db` | swaps in the shared-cache engine so real `asyncio.to_thread` writes can race the test's reads | tests that run the *real* polling loop |
| `tmp_path` (autouse chdir) | cwd is a throwaway dir | anything writing relative paths |

Every test is isolated by the autouse `_patch_engine`: session factory
override, `_overlay.clear()`, storage-root wipe, truncate all tables, rebind
`LocalStorageBackend`, drop the cached `httpx.AsyncClient`, reset the login/
refresh rate limiters. **Adding a module-level singleton to `app/`? Add its
reset to `_patch_engine` in the same PR** — otherwise it leaks under
`pytest-randomly` and shows up as an order-dependent failure far from its cause.

E2E fixtures (`tests/e2e/conftest.py`): `e2e_db` (on-disk SQLite so app
handlers and worker threads get their own connections), `api` (async
`httpx.AsyncClient` over `ASGITransport`), `fakes` (`Recorder` + `base_url`
for Discord/ntfy/webhook/Telegram targets, `flaky_webhook_url(key)`),
`superuser_headers`. Drive `notifications.dispatch_due()`, hub methods, and
scan functions directly — background loops are deliberately not started.

## Configuration in tests

`Settings` reads `VAULT_*` once at import. Per-test configuration goes through
the overlay, never `os.environ`:

```python
from app.core.config import _overlay

def test_purges_trash_after_retention(monkeypatch, db_session):
    monkeypatch.setitem(_overlay, "trash_retention_days", 0)
    ...
```

`monkeypatch.setitem` restores on teardown; `_patch_engine` also clears the
overlay before the next test, so a bare `_overlay[...] = ...` inside a fixture
with a `pop` in teardown (the older pattern in the suite) is equivalent — pick
`monkeypatch` for new code. Storage dirs: `_overlay["data_dir"] = tmp_path /
"files"` and `get_backend()` after `bind_backend(LocalStorageBackend())` (see
`test_ingestion_atomicity.py::storage`).

## Standing in for egress

Only outbound boundaries are ever faked, and only in pure/in-process tests:

- **Outbound HTTP** — patch `get_http_client` **where it is used**, not where
  it is defined: `patch("app.services.moonraker.get_http_client")`, then
  `.return_value.request = AsyncMock(return_value=resp)` /
  `side_effect=[...]`. Build responses as `MagicMock(status_code=..., json=...,
  text=...)` or real `httpx.Response`. Assert the *reaction* (raised
  `ProviderError`, persisted state) — and the request URL/body only when the
  request *is* the contract under test.
- **Provider transports with constructor seams** — inject the fake:
  `BambuLanProvider(..., mqtt_client_factory=factory)` from
  `tests/e2e/fakes/mock_bambu.py`; `PrintSim(monotonic=lambda: now[0])` for a
  controllable clock.
- **Clock** — inject via the seam the code offers (`PrintSim.monotonic`,
  `app.core.time`), or `monkeypatch.setattr` at the using module. Pin
  absolute instants; never derive expectations from `datetime.now()`.

Never patch inside `*_integration.py` or `tests/e2e/`. Those files reach
faults through the fakes' own flags.

## Emulators and the testkit

`printstash_core_testkit` (in `backend/packages/printstash-core/src/`) is the
shared toolbox; `tests/e2e/fakes/*` re-exports it and adds the transports that
need app-side types:

| Need | Reach for |
| --- | --- |
| Real loopback ASGI server for a fake | `start_server(app) -> RunningServer(base_url, stop)` |
| What the fake received, with call counts | `Recorder` / `Received` |
| A printer that progresses in wall-clock or injected time | `PrintSim(total_mm, total_seconds, print_seconds, monotonic=)`; states `STANDBY/PRINTING/PAUSED/COMPLETE/CANCELLED/ERROR` |
| Moonraker + Spoolman, PrusaLink, OctoPrint apps | `mock_printer.create_app`, `mock_prusalink.create_app`, `mock_octoprint.create_app` |
| Notification targets (Discord/ntfy/webhook/Telegram, flaky) | `build_provider_app(recorder)` |
| Bambu MQTT/FTPS, Centauri SDCP, OIDC provider | `tests/e2e/fakes/mock_bambu.py`, `mock_centauri.py`, `mock_oidc_provider.py` |

Faults are flags on the fake (`reject_commands=True`,
`expected_access_code=...`, `--auth-mode`, `/flaky/{key}`, a `PrintSim` driven
to `ERROR`). When the fault you need has no flag, add one to the fake — that's
a contract-fake improvement, not a test hack.

A new provider: add its credentials row to `FULL_CREDENTIALS` in
`test_provider_conformance.py` (the pack fails until you do), write
`test_<provider>.py` for normalisation, `test_<provider>_integration.py`
against its emulator, and one `tests/e2e/` flow. Update
`docs/provider-support.md` for the support level you actually proved.

## Async

`pytest-asyncio` is in strict mode: mark coroutine tests `@pytest.mark.asyncio`
(e2e style) or call `asyncio.run(coro)` inside a sync test (provider-client
style). Both are established. The cached outbound client is dropped per test,
so a fresh `asyncio.run()` loop never inherits one bound to a dead loop.

Waiting on a background job in e2e: bounded poll with a short sleep and a hard
failure at the end (`_await_job` in `test_e2e_ingest.py`). Elsewhere, drive
the entrypoint directly instead of sleeping.

## Concurrency

- Real cross-thread DB traffic (hub polling loop + test reads): `threaded_hub_db`.
- Simultaneous writes / unique-constraint races: `ThreadPoolExecutor` +
  `threading.Barrier` so both writers reach the statement together
  (`tests/postgres/test_postgres_contracts.py`). Assert the row count and the
  `IntegrityError`, not timing.

## PostgreSQL

`tests/postgres/` skips without `PRINTSTASH_TEST_POSTGRES_URL`; CI supplies a
real `postgres:16`. Add a case there when a change introduces a query, index,
constraint, or migration whose behaviour can differ from SQLite (JSON
operators, `ON CONFLICT`, boolean/enum handling, batch alters). Run it locally
against a real server before claiming the change is Postgres-safe.

## Contract snapshots

- `tests/fixtures/openapi_contract.json` — any router/schema change fails
  `test_openapi_contract.py`. Regenerate with `UPDATE_OPENAPI_CONTRACT=1 uv run
  pytest tests/test_openapi_contract.py`, **read the diff**, and mention the
  API change in the PR Notes. Additive only within 0.x.
- `frontend/src/generated/printer-contracts.ts` — provider capability changes
  in `printstash_core` regenerate via `python -m
  printstash_core.printers.codegen --output ... --check` (CI enforces).

## Coverage gate

CI runs `./scripts/test.sh full --cov=app --cov-fail-under=95`. A new branch
without a test lowers the number; add the test. `# pragma: no cover` is
reserved for the S3 paths the `storage-s3` job validates for real — not a
tool for skipping hard cases.

## Lint and types

`uv run ruff check app/ tests/` and `uv run ruff format app/ tests/` apply to
tests too; `uv run pyright` covers `app/`. Type your helpers
(`-> dict[str, str]`), keep `from __future__ import annotations` at the top,
and import inside the test body only when the import has side effects the
module docstring explains (the conftest does this for settings ordering).
