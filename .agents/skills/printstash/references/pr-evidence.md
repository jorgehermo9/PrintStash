# PR visual evidence

A PR that changes what a user sees or does in the web UI shows the change: a
**screenshot** for a state, a **video** for a flow, an interaction, or motion.
The reviewer sees the result without checking out the branch. Backend-only,
docs, and test-only PRs mark it `not needed`.

Evidence lives in a scratchpad and in the PR description, never in git. The
throwaway specs (`frontend/tests/pr-evidence/`) and the default output are
gitignored, `PR_EVIDENCE_OUT` inside the repository is refused, and
`frontend/tests/repo/pr-evidence.test.ts` fails if any video is tracked.

## Capture

The stack is the real-backend Playwright suite's (throwaway SQLite, Vite dev
server, mock printer, a `page` signed in as the seeded admin), so frames show
seeded data only: no real library, printer host, or token.

1. Copy the templates from [pr-evidence/](pr-evidence/) into the gitignored
   spec directory:

   ```bash
   mkdir -p frontend/tests/pr-evidence
   cp .agents/skills/printstash/references/pr-evidence/* frontend/tests/pr-evidence/
   ```

   - `screenshot.evidence.ts`: full-page and element screenshots of one state.
   - `video.evidence.ts`: setup on `page`, then `record()` opens a recorded
     1280×720 page so `flow.webm` holds only the flow. `slowMo` paces it.
   - `evidence.ts`: the shared `test` (hides the React Query devtools button),
     `record()`, `settle()`, and `hold()`, which keeps a state on screen long
     enough to read.
   - `frames.mjs`: samples frames from a video (see below).

2. Replace the steps with the PR's change. Build state with the `e2e-real/util.ts`
   helpers (`uploadModel`, `modelCard`, `openFilters`, …). Call `settle()` before
   every capture: a route passes its first assertion while its content is still
   loading, and the frame comes out blank.

3. Run it with the output in your scratchpad:

   ```bash
   cd frontend
   PR_EVIDENCE_OUT=<scratchpad>/evidence pnpm evidence [screenshot.evidence.ts]
   ```

   Each run empties `PR_EVIDENCE_OUT` first, so run every spec the PR needs in
   one go, or copy out what you keep.

## Look before attaching

Open every PNG, and sample every video with
`node tests/pr-evidence/frames.mjs <flow.webm> <out-dir>` (no ffmpeg needed).
Done when each file shows the changed UI fully rendered: no loading skeleton, no
blank region, no dev tooling, nothing private. Keep a video under 30 seconds;
GitHub caps images and free-plan videos at 10 MB.

## Attach

`gh` 2.99+ uploads media with `--attach` on `gh pr create`, `gh pr edit`, and
`gh pr comment`. A body reference such as `![Filters open](./01-page.png)` is
rewritten to the uploaded asset in place; anything unreferenced is appended.
Videos render as a player and take no alt text.

```bash
gh pr edit <n> --body-file body.md \
  --attach '<scratchpad>/evidence/<test>/01-page.png#Model detail page' \
  --attach '<scratchpad>/evidence/<test>/flow.webm'
```

Uploading needs write access to the repository the PR targets. From a fork
without it, hand the files to the human (`SendUserFile`, or name the paths) to
drag into the description.
