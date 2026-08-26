# Frontend unit tests (vitest)

Conventions for `frontend/src/**/__tests__/**` and
`frontend/packages/{ui,domain}/src/__tests__/**`. Policy is in
[SKILL.md](../SKILL.md); this is the runtime-specific how.

## Where a test lives

- **Mirror by basename, co-located**: `src/<dir>/<module>.ts(x)` ↔
  `src/<dir>/__tests__/<module>.test.ts(x)`. One test file per module; a
  component folder (`src/components/model-detail/`) gets its own `__tests__/`.
  Workspace packages own theirs (`packages/ui/src/__tests__/`,
  `packages/domain/src/__tests__/`). A test file whose basename matches no
  source module is testing something that isn't a unit — find the unit.
- `pnpm test` runs the root project and both packages; a package test that
  isn't under `src/**/*.{test,spec}.{ts,tsx}` is silently skipped.
- `vitest.fast.config.ts` is an **audited** shared-worker lane for pure files
  (no DOM globals, no module mocks, no fake timers, no process-global state).
  Add a file there only after it passes repeat + shuffle in isolation; the
  authoritative suite stays `vite.config.ts`.
- Design-system snapshots: only for `@printstash/ui` primitives
  (`public-api.test.tsx`). Never snapshot a page.

## Environment

jsdom, `globals: true`, `vitest.setup.ts` runs `cleanup()` and
`localStorage.clear()` after every test. There is **no** `mockReset`/
`unstubEnvs` config, so each file owns its reset:

```ts
const fetchMock = vi.fn<typeof fetch>();

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
  invalidateApiCache(); // request.ts keeps a 30s GET cache under TanStack Query
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
```

Hooks go inside the `describe` they serve when a file has several; a
single-unit file may keep them at module scope (both shapes exist; match the
file you're in).

## Stand-ins: inject, never module-mock

`vi.mock` / `vi.doMock` are lint errors (`anti-slop/no-module-mocking`). The
app is built so the seams are injectable — use them:

| Boundary | Stand-in |
| --- | --- |
| The API (component through the real api client) | `vi.stubGlobal("fetch", vi.fn<typeof fetch>())`; build **real** `Response` objects (`new Response(JSON.stringify(x), { status, headers })`); a body reads once, so `mockImplementation(() => Promise.resolve(fresh()))` |
| Query hooks | `<QueryApiProvider api={{ ...defaultQueryApi, ...stubs }}>` with `vi.fn<QueryApi["listPrinters"]>()` per read the test performs |
| Server state a page renders | seeded `QueryClient` — `client.setQueryData(queryKeys.printers, [...])` with `{ retry: false, staleTime: Infinity, refetchOnWindowFocus: false }` so nothing falls back to the network |
| Auth | `<AuthContext.Provider value={ADMIN_AUTH}>` with typed `vi.fn<AuthState["login"]>()` members |
| Routing | `<MemoryRouter initialEntries={[...]}>` |
| Browser globals (`matchMedia`, `IntersectionObserver`, clipboard) | `vi.stubGlobal(name, fake)` in `beforeEach`; unstub in `afterEach` |
| Persistence | real `localStorage`/`sessionStorage` (cleared for you) |

Typing rules (also enforced by anti-slop): `vi.fn<typeof fetch>()`,
`vi.fn<QueryApi["getVaultStats"]>()`, `satisfies SomeInterface` for hand-rolled
fakes. `as unknown as X` and chained `as` are lint errors; a `SAFETY:` comment
must state the specific invariant for that one site. When a rule fires, fix
the typing — never suppress.

## What to assert

- **Rendered outcome** through roles and labels: `screen.getByRole("button",
  { name: "Upload" })`, `getByText`, `toBeVisible()` / `toHaveCount(0)`. No
  class-name selectors — `DESIGN.md` tokens change; roles don't.
- **The request the app sent** when the HTTP contract is the behaviour: filter
  `fetchMock.mock.calls` by method → `{ url, body }` and assert the exact
  payload the backend router reads (`printers-list.test.tsx::requestsWithMethod`).
  This is the boundary observable, not an implementation detail.
- **Store/state effects**: `getToken()`, `consumeSessionExpired()`, the
  persisted preference — not that a setter ran.
- **Hooks**: `renderHook` with a wrapper providing `QueryClientProvider` +
  `QueryApiProvider`; `await waitFor(() => expect(result.current.data)...)`.
- **Interaction**: `userEvent.setup()`; wrap state-changing async in `act`
  only when RTL warns.

Explicit "does not throw" only where not-throwing *is* the contract
(`expect(() => parseLenient(garbage)).not.toThrow()`).

## Timers

No virtual clock is installed. `vi.useFakeTimers()` is allowed only when the
code under test owns the timer (debounce, polling interval); restore with
`vi.useRealTimers()` in `afterEach`, and advance with
`vi.advanceTimersByTimeAsync` when the code `await`s between ticks. Pin
`Date` fixtures as absolute ISO strings; never build one from `Date.now()` at
module scope.

## File anatomy (vitest specifics)

Follow the anatomy in SKILL.md. In vitest:

```ts
/**
 * request.ts keeps a 30s GET cache with in-flight dedup under TanStack Query.
 * These tests pin that contract: hits skip the network, concurrent calls share
 * one request, `fresh` bypasses, any mutation clears.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getJson, sendJson } from "@/lib/api/request";

const FROZEN_NOW = "2026-01-01T00:00:00Z";
const fetchMock = vi.fn<typeof fetch>();

function jsonResponse(data: JsonValue, status = 200): Response { ... }   // shared builder

describe("getJson", () => {                       // ↔ the exported unit, in source order
  beforeEach(() => { vi.stubGlobal("fetch", fetchMock); fetchMock.mockReset(); });
  afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

  it("returns the parsed body on 200", async () => { ... });          // Happy
  it("serves a repeat call from cache without a second fetch", ...);  // Edge
  it("rejects with ApiError carrying the status on 500", ...);        // Error
});

describe("sendJson", () => { ... });
```

- Top-level `describe` = the exported unit; nested `describe` = a method or a
  scenario group when a unit has many; `it` reads as one behaviour.
- Hooks live inside the `describe` they serve; a single-unit file may keep
  them at module scope.
- Component tests: one `render<Component>(seed)` helper per file that wires
  the real providers (`QueryClientProvider`, `AuthContext`, `MemoryRouter`);
  tests differ only in seed and interaction.

### `it.each` / `describe.each`

Same rule as SKILL.md: one behaviour, several inputs, identical assertion
shape. Use object cases with a `label` so the name reads as the variant:

```ts
it.each([
  { label: "bytes", input: 512, expected: "512 B" },
  { label: "kibibytes", input: 2048, expected: "2.0 KiB" },
  { label: "zero", input: 0, expected: "0 B" },
])("formats $label", ({ input, expected }) => {
  expect(formatBytes(input)).toBe(expected);
});
```

`describe.each` for sweeping a component across a registry (every provider
in `PRINTER_PROVIDERS`, every locale) — derive the array from the production
constant, never copy it. A case whose assertion differs is its own `it`.

## Repo-specific gates a test change can trip

- `i18n-coverage.test.ts` — every user-facing string needs a key in each
  locale; tests render the English default.
- `changelog.test.ts` — `CHANGELOG[0].version` must equal `package.json`
  version (release commits only).
- `printer-contracts.test.ts` — `src/generated/printer-contracts.ts` is
  codegen output; regenerate from `printstash_core`, never hand-edit.
- `pnpm lint` (oxlint, `--deny-warnings`) and `pnpm format:check` (oxfmt) run
  on tests too. Run `pnpm format`; don't hand-format.

## Validate

```bash
cd frontend
pnpm lint && pnpm format:check && pnpm typecheck
pnpm test                      # root + @printstash/ui + @printstash/domain
pnpm test:coverage             # informative floor in vite.config.ts; don't let it drop
```
