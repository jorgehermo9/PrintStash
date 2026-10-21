# Release procedure

Canonical validation detail: `docs/release-validation.md` (clean install,
upgrade-from-volume, smoke checks) and `docs/manual-testing.md` (full browser
sweep). This file is the ordered checklist that ties it together.

## Readiness gate

- [ ] Confirm the release scope. Every planned bug or feature is merged through
      its own PR to `main`, together with the migrations, documentation, and
      validation that change requires.
- [ ] `main` is up to date and its required `PR gate` is green. Release from
      this integrated state; do not collect work on a version-number branch.
- [ ] Choose X.Y.Z from the merged contents. While 0.x, patches contain fixes
      only; a release containing features increments the minor version.
- [ ] If protected `main` requires a PR for release metadata, create
      `chore/release-X.Y.Z` from the current `main`. This branch may contain only
      the version, changelog, and release-documentation edits below.

## Prepare the release commit

- [ ] Bump the triple (`backend/pyproject.toml`, `backend/app/core/config.py`
      `app_version`, `frontend/package.json`) **and** add a matching
      `CHANGELOG[0]` entry to `frontend/src/lib/changelog.ts` (its own test,
      `changelog.test.ts`, checks this against `package.json` and fails CI on
      its own if skipped) — one commit: `chore(release): bump to X.Y.Z`.
- [ ] Bump the app-store manifests under `catalogues/` to the new version in
      the same commit (`tests/repo/test_catalogue_manifests.py` fails until
      they match). The stores themselves are updated after publishing (below).
      The TrueNAS package is the exception: it lives in `truenas/apps`, so it is
      bumped after the tag, in the TrueNAS section below.
- [ ] Promote the accumulated `CHANGELOG.md` `## Unreleased` contents to
      `## X.Y.Z`, restore an empty `## Unreleased`, and verify the entry matches
      the condensed in-app changelog (format in
      [conventions.md](conventions.md)).
- [ ] Run focused local checks for the release-only metadata changes. The
      merged commit's `CI` result covers the regular PR gate; `Deep CI` below
      covers full suites and coverage without repeating them locally.
- [ ] Browser extension, when changed: run affected local tests and browser
      flows per [capture.md](capture.md); `CI` builds and tests the package,
      and `Deep CI` runs its real-backend and ChromeDriver checks.
- [ ] Upgrade check: previous-release DB → `uv run alembic upgrade head` →
      app boots (self-hosters upgrade from old releases; CI has a
      migration-upgrade job, but run it locally for schema-heavy releases).
- [ ] Compose smoke: `docker compose up` →
      `/api/v1/health` returns the new version.
- [ ] If the release touches a provider: add a Hardware Validation Log row in
      `docs/provider-support.md` from a real smoke test, or carry the
      "needs real-world hardware validation" note in
      `docs/known-limitations.md`. Never leave it silently implied as done.
- [ ] Docs sweep: `docs/provider-support.md`, `docs/known-limitations.md`,
      `docs/roadmap.md`, docs pages the release invalidates — docs now live
      in the `printstash-landing` repo's `src/content/docs/docs/` (built as
      `/docs` on the same site, not a separate wiki repo) — (plus the
      gitignored `docs/feature-inventory.md` if you have it locally).

## Publish

- [ ] If a release-metadata PR was required, merge it to `main`; then update the
      local `main` and verify its HEAD contains the version triple and changelog.
- [ ] Run `Deep CI` manually on that exact `main` commit after all metadata
      changes have merged. Wait for a green result. The release workflow checks
      both `CI` and `Deep CI` for the tag's exact SHA and will stop on an absent,
      failed or running result; it does not re-run the suites.
- [ ] Tag that exact `main` commit:
      `git tag vX.Y.Z && git push origin vX.Y.Z`. CI publishes the GHCR image
      and the tag guard checks the version triple.
- [ ] `gh release create vX.Y.Z` with the format below.
- [ ] Update the app stores, following each store's section of
      `catalogues/README.md` (it is the source of truth for paths, pinning and
      per-store checks):
      - Wait for the image: `docker buildx imagetools inspect
        ghcr.io/xiao-villamor/printstash:X.Y.Z` lists `linux/amd64` and
        `linux/arm64`; note the digest for Umbrel.
      - **Confirm with the maintainer before opening any store PR or pushing to
        the Runtipi store.** They are public actions in other projects'
        repositories.
      - Umbrel: the ordered steps below, then a PR to
        `getumbrel/umbrel-apps` through that repository's own
        `umbrel-update-app` and `umbrel-test-app` skills.
      - CasaOS / ZimaOS: a PR to `IceWhaleTech/CasaOS-AppStore`, validated with
        its `./scripts/build_dist.sh`.
      - Cosmos Cloud: a PR to `azukaar/cosmos-servapps-official`, after
        `node .github/scripts/validate-servapps.js PrintStash` passes in the fork;
        the package is `catalogues/cosmos/PrintStash/`.
      - Runtipi: push the updated app to the PrintStash Runtipi app store
        (`xiao-villamor/printstash-runtipi`) with `tipi_version` incremented.
      - TrueNAS: a PR to `truenas/apps` moving `app_version`, the image digest
        and the app `version` in `ix-dev/community/printstash/`; the ordered
        steps are in the TrueNAS section below.
      - Unraid: nothing to do; Community Applications re-reads the template.
      - Link each store PR from the GitHub release notes, and record anything a
        store reviewer changed (for example a reassigned port) back in
        `catalogues/`.
- [ ] Announce in the public roadmap discussion. Changelog says what's
      protected; never quotes private `reports/` analysis.
- [ ] Update the "Where we are" block in `SKILL.md` (this skill).

## Umbrel App Store, every release

`catalogues/umbrel/printstash/` and `printstash/` in `getumbrel/umbrel-apps` are
the same package: the catalogue is the source, the store copy is what umbrelOS
installs. Both move in one release, or the listing keeps serving the old image.
`catalogues/README.md` holds the field-by-field contract; this is the order.

1. Read the digest of the published image:
   `docker buildx imagetools inspect ghcr.io/xiao-villamor/printstash:X.Y.Z`
   must list `linux/amd64` and `linux/arm64`. Copy the digest it reports for
   that tag; it is the multi-arch index digest, not a per-architecture one.
2. In the release commit, bump `version` and set
   `image: ghcr.io/xiao-villamor/printstash:X.Y.Z@sha256:<digest>`.
   `tests/repo/test_catalogue_manifests.py` fails until the tag equals the app
   version and the digest is a full-length one, so the two cannot drift.
3. Replace `releaseNotes` with this release's notes from the CHANGELOG entry:
   user-visible features, fixes, and any upgrade or backup action, plus the
   upstream release link. Keep two blank lines between paragraphs and one
   before and between list items; YAML folds single newlines into spaces.
   Never leave the previous version's notes in place, and never write the
   notes only in the store copy.
4. Copy `catalogues/umbrel/printstash/` over `printstash/` in your fork and
   follow `umbrel-update-app`. `submission` keeps pointing at the first PR;
   the store does not track later ones. Leave `gallery`, `icon` and
   `storage: dataRoot: data` as they are.
5. `npm run lint:apps -- printstash --check-images` must pass, and its digest
   check must resolve to the digest in the compose file, then `git diff
   --check`.
6. Test through `umbrel-test-app`: install on umbrelOS, sign in with the
   credentials Umbrel shows, choose storage, upload a model, restart, and
   confirm the library survives. A release that cannot be installed that way
   is not ready.
7. Open the PR, link it from the GitHub release notes, and record anything
   the maintainers change (a reassigned port, a pinned image, edited copy)
   back into `catalogues/umbrel/printstash/` in the same release. Otherwise
   the next release reintroduces it.

## TrueNAS community catalog, every release

The TrueNAS Apps catalog is a repository, `truenas/apps`, and its community
train is a directory per app. PrintStash lives in
`ix-dev/community/printstash/`, so this is the one listing whose source is not
under `catalogues/` in this repository: the package there is the source, and
this section is the procedure. A TrueNAS system reads the train to render its
Apps screen, so a stale entry is an installable old version.

1. Wait for the image, as for the other stores:
   `docker buildx imagetools inspect ghcr.io/xiao-villamor/printstash:X.Y.Z`
   must list `linux/amd64` and `linux/arm64`. The package itself is not
   architecture-specific, but TrueNAS hosts are `linux/amd64`.
2. In a branch of your fork of `truenas/apps`, set the tag and the multi-arch
   digest together in `ix-dev/community/printstash/ix_values.yaml`:
   `tag: "X.Y.Z@sha256:<digest>"`. The catalog's own apps pin digests, and its
   `validate_images` check refuses a digest only on registries that
   garbage-collect manifests (quay.io), so a ghcr pin is what it expects.
3. In `ix-dev/community/printstash/app.yaml`, set `app_version: X.Y.Z` and bump
   `version` (the catalog's own version, patch level per release). Leave
   `date_added` alone.
4. Regenerate the derived metadata from a checkout of `truenas/apps`:
   `python3 ./.github/scripts/generate_metadata.py --train community --app
   printstash`. It needs Docker (it renders through the validation image) and it
   rewrites `app.yaml` and `item.yaml`. Commit what it writes; the repository's
   `check_metadata` job fails when the committed files differ from its output.
5. Run the app test before opening the PR:
   `./.github/scripts/ci.py --app printstash --train community --test-file
   basic-values.yaml --wait=true`. The CI `app-test-suite` workflow runs the same
   deploy on the PR, but a release that cannot start is worth finding first.
6. Open the PR from the fork with the repository's `app_addition.md` template for
   the first submission and its default template afterwards, stating that it is
   an update, the app version, and what was tested. Link it from the GitHub
   release notes like the other stores.
7. Record anything the maintainers change (a reassigned default port, edits to
   the install notes or the question descriptions) in `truenas/apps` itself.
   Nothing about the TrueNAS package is duplicated in this repository, so there
   is nothing here to keep in sync.

Two things the package depends on, so a release that changes either needs the
package changed with it: the container port `3000` (the template maps the
published port onto it, because the image's nginx config fixes it), and the
`PUID`/`PGID` runtime identity (the template sets the container user, and the
library passes both variables from that answer). A release that moves the data
root out of `/data`, or that stops reading `PUID`/`PGID`, breaks the listing
until the template follows. Everything else, including a new environment
variable, needs no TrueNAS change: the install form carries an Additional
Environment Variables group.

## GitHub release format

Match existing releases (`v0.8.3`, `v0.8.1`):

- **Title:** `vX.Y.Z — short theme` (a few words; omit the theme only if the
  release has no unifying one).
- **Body:** the `CHANGELOG.md` entry for that version, `###` headers
  (Security/Fixed/Performance/Added as applicable). Leading bold callout line
  only for upgrade-behavior warnings. No `##`/`###` version header at the top
  — the title already carries the version.
- **End with:**
  `**Full changelog:** https://github.com/xiao-villamor/PrintStash/compare/vPREV...vX.Y.Z`

Example title: `v0.8.5 — CI/ops hardening`.
