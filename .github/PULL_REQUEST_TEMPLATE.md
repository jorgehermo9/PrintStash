## Summary

- 

## Testing

- [ ] `cd backend && ./scripts/test.sh full` green, or not needed
- [ ] `cd frontend && pnpm lint && pnpm format:check && pnpm typecheck && pnpm test` green, or not needed
- [ ] Manual test notes included, or not needed

## Coverage matrix

<!-- Required whenever tests were added or changed — a one-line assertion edit
counts. One row per observable behaviour, which is also one test function.
Derive the rows from requirements (the issue, CONTEXT.md, docs/provider-support.md,
the router contract), never from reading the implementation: matrixing what the
code happens to do reproduces its bugs as "expected".

Every row ends at ✅ `<tier dir>/<file>::<test>`, ❌ missing, or ⏭️ N/A — reason.
Zero unexplained ❌ is the definition of done.

Format: .agents/skills/create-tests/SKILL.md -->

| # | Behaviour | Category | Precondition / input | Observable outcome | Tier | Status |
|---|-----------|----------|----------------------|--------------------|------|--------|
|   |           |          |                      |                    |      |        |

### Tier check

<!-- The directory is the tier. Tick the ones this PR added to. -->

- [ ] `unit/` — pure logic, or a fault that cannot be reproduced for real
- [ ] `integration/` — **the default**: any router, any DB write, any RBAC or live/trashed rule
- [ ] `contract/` — our client against a real fake over a loopback socket, with **no mocking inside**
- [ ] `e2e/` — one test for the feature's headline capability
- [ ] `frontend/src/**/__tests__/` or `frontend/tests/e2e-real/`

### Self-check

- [ ] Every new test file opens with a contract header saying what it defends
- [ ] Every new test asserts **one** behaviour — no name contains "and"
- [ ] No `skip`/`xfail`/`.skip` without an issue URL, and no commented-out tests
- [ ] Every new endpoint has a 401 row and, if it is role-gated, a 403 row

## Notes

Mention any schema, API, storage, or printer-provider behaviour changes.
