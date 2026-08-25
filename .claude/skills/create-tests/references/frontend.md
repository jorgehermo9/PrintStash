# Frontend unit tests (vitest)

Conventions for `frontend/src/**/__tests__/**` and
`frontend/packages/{ui,domain}/src/__tests__/**`. Policy is in
[SKILL.md](../SKILL.md); this is the runtime-specific how.

## Where a test lives

- Beside the unit: `src/lib/__tests__/<module>.test.ts`,
  `src/components/__tests__/<component>.test.tsx`,
  `src/pages/__tests__/<page>.test.tsx`. Workspace packages own theirs
  (`packages/ui/src/__tests__/`, `packages/domain/src/__tests__/`).
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

## Naming

Top-level `describe` = the unit (component, hook, module); nested `describe`
= method or scenario group; `it` reads as one behaviour: `it("expires an
established session once across concurrent 401 responses")`. A file-level
comment states the contract the file locks down (see `request.test.ts`,
`queries.test.tsx`).

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
