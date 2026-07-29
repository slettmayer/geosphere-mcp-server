# Changelog and release hygiene

**Date:** 2026-07-29 · **Applies to:** `slettmayer/geosphere-mcp-server`, `slettmayer/oebb-mcp-server`

Both repos get the same changes. This single spec covers both; it lives here because this repo holds the
established `docs/` architecture.

## Problem

`CHANGELOG.md` drifted out of sync with what was actually published. As of 2026-07-29, before the fix:

- geosphere had shipped 0.1.1, 0.1.2 and 0.2.1 to PyPI with **no changelog section** for any of them
- oebb had shipped 0.1.2 and 0.1.3 the same way, and carried a `## 0.1.0` heading for a version that was
  never tagged and never published
- entries for released work sat under `## Unreleased`, so the file understated what users already had

Two independent causes, and a fix addressing only the first leaves half the problem alive:

1. **Automated releases skip the changelog.** `auto-release.yml` cuts a patch tag whenever a Dependabot
   `uv` PR merges, and never touches `CHANGELOG.md`. This produced every missing section above.
2. **Manual releases depend on memory.** Cutting a feature release means renaming `## Unreleased` to the
   new version and tagging. Nothing documented or enforced this, so it silently did not happen — which is
   why entries accumulated rather than being filed under the version that shipped them.

## Goals

- Every published version has a changelog section, with no human required to remember a step
- `## Unreleased` contains only genuinely unreleased work
- Dependency bumps — the entries nobody ever writes by hand — are generated
- Deliberate minor/major releases are a one-click operation, not a manual tag
- The release-critical logic is testable without cutting a release

## Non-goals

- Full conventional-commit changelog generation. Human-written prose stays human-written; only
  Dependabot's commits are generated.
- Blocking merges on changelog entries. The PR-time check warns; it never gates.
- Sharing the workflow across repos via a reusable workflow. With two repos, keeping each self-contained
  is worth the duplication; revisit at three or more.

## Design

### 1. Release-time rename (closes both causes)

`auto-release.yml` already has both triggers that can cut a release: a merged Dependabot `uv` PR, and
`workflow_dispatch`. Moving the changelog rename into that workflow means **every** release takes the same
path and no human ever performs the rename — which is what closes cause 2, not just cause 1.

New job flow, replacing the current compute-version → tag → push:

1. Generate the GitHub App token (unchanged) and check out `main` with full history.
2. **Determine the version.**
   - `workflow_dispatch` with a `version` input → use it, validated against `^v[0-9]+\.[0-9]+\.[0-9]+$`
   - otherwise → patch bump from the highest existing `v*` tag (current behaviour)
3. **Rewrite `CHANGELOG.md`** by invoking `scripts/changelog_release.py` (see below).
4. **Commit** the rewritten `CHANGELOG.md` to `main` — but only if the file actually changed
   (`git diff --quiet -- CHANGELOG.md`). The script is a no-op when the section already exists, and an
   unconditional `git commit` would fail on an empty index and abort an otherwise valid release.
5. **Tag** the resulting commit — the new one if step 4 committed, otherwise `main`'s current head — then
   push commit and tag together.

Step 5's ordering is load-bearing: the tag must point at the commit *containing* the changelog. Tagging
`main`'s previous head — what happens today — would ship every release with a changelog missing its own
section.

`release.yml` needs no changes. It triggers on the `v*` tag push and builds from the tagged commit, which
now includes the section.

#### `workflow_dispatch` version input

Add an optional `version` input (e.g. `v0.4.0`), defaulting to empty = patch bump. Today the workflow can
only compute a patch, which is why the 0.3.0 / 0.2.0 minor releases had to be tagged entirely by hand.

### 2. `scripts/changelog_release.py`

The rewrite logic lives in a tested Python script rather than inline YAML. A release workflow that is only
exercised during a release is discovered to be broken at the worst possible time; both repos already have
pytest and a `dev` dependency group.

**Interface**

```
python scripts/changelog_release.py --version v0.4.0 [--date 2026-07-29] [--repo-root .]
```

Writes `CHANGELOG.md` in place. Exits non-zero with a message on any precondition failure.

**Behaviour**

1. Read `CHANGELOG.md`; locate the `## Unreleased` heading. Absent → error (the file's contract is broken;
   fail loudly rather than guess).
2. **Idempotency check:** if a `## <version>` section already exists, make no changes and exit 0. This is
   required — a human may have written the section by hand as part of the release PR (as happened for
   0.3.0 / 0.2.0), and the workflow must not duplicate it.
3. Collect the body under `## Unreleased` (everything up to the next `## ` heading) as the human entries.
4. Derive dependency entries: for each commit authored by `dependabot[bot]` in the range
   `<highest-existing-tag>..HEAD` — or across all history when no `v*` tag exists yet — emit one
   `- Build: <subject>` line. Normalisation: strip the trailing ` (#NN)`, strip a leading
   `chore(deps):` / `build(deps):` / `chore(deps-dev):` prefix, lowercase the first remaining character,
   and ensure a trailing period.
   - Author-matching is deliberate. Squash merges preserve `dependabot[bot]` as commit author (verified),
     whereas the subject prefix differs between the two repos, so subject-matching would be brittle.
5. Compose the new section: `## <version stripped of leading v> - <date>`, then human entries, then
   derived `- Build:` lines.
6. If both are empty, write a single `- Build: dependency updates.` line. A version must never get an
   empty section.
7. Replace `## Unreleased` with an empty `## Unreleased` followed by the new section.

**Tests** (`tests/test_changelog_release.py`), against fixture changelog strings:

- human entries only → renamed correctly, `Unreleased` left empty
- dependabot commits only → generated `- Build:` lines, prefix and `(#NN)` stripped
- both → human entries first, then `- Build:` lines
- neither → the `dependency updates.` fallback
- version section already present → file unchanged, exit 0 (idempotency)
- missing `## Unreleased` → non-zero exit
- the date lands in the heading and the version's leading `v` is stripped
- subject normalisation: `chore(deps): Bump aiohttp in the group (#14)` → `- Build: bump aiohttp in the group.`
- no `v*` tag exists yet → derives across all history instead of erroring on an empty range

Git access is injected (a function returning commit subjects) so tests need no repository fixture.

### 3. Warn-only PR guard

A new `changelog` job in `validate.yml`:

- runs on `pull_request` only
- compares changed files against the base ref
- if any `src/**` file changed and `CHANGELOG.md` did not, emits a `::warning::` annotation
- **always exits 0**, and is deliberately **not** listed in `gate`'s `needs`, so it can never block a merge
- skipped when the PR author is `dependabot[bot]`, or the PR carries a `no-changelog` label

The point is to surface the omission while it is still cheap to fix, without adding ceremony to dependency
bumps or docs-only PRs.

### 4. Documentation

Currently no repo documents the release process at all — `docs/README.md` covers the docs architecture
only, which is part of why cause 2 went unnoticed.

Add `docs/tech/RELEASING.md` to each repo, covering:

- the version comes from the git tag via `hatch-vcs`; there is no version string in the source tree
- entries go under `## Unreleased` as work lands
- how a release is cut: `workflow_dispatch` on Auto Release, optionally with a `version` for minor/major
- what merging a Dependabot `uv` PR does automatically
- that `github-actions` bumps do not cut a release

Index it in `docs/tech/README.md`, and add a one-liner plus link in `AGENTS.md` (geosphere) /
`CLAUDE.md` (oebb).

## Failure modes

| Risk | Handling |
|---|---|
| Branch protection added to `main` later | The workflow pushes to `main`, unprotected today. Adding the handover's rule needs App-token bypass or this breaks. Noted in `RELEASING.md`. |
| Workflow re-triggering itself | None. `auto-release.yml` triggers on `pull_request: closed` and `workflow_dispatch`, not `push`, so its own commit cannot start another run. |
| Human already wrote the section | The idempotency check in step 2 makes the script a no-op. |
| Extra CI run from the changelog commit | `validate.yml` runs on `push` to `main`; the commit triggers one harmless extra run. Accepted. |
| Dependabot changes its author attribution | Derivation yields nothing; the section falls back to human entries or the generic line. Degrades quietly rather than failing the release. |
| `github-actions` bumps appearing as `- Build:` noise | Accepted. They were genuinely part of that release; the line is accurate if uninteresting. |

## Rollout

1. Land the script plus tests, the workflow changes, the guard and the docs in one PR per repo. oebb first
   as the canary — it has the simpler validate workflow and fewer tests.
2. Verify by running Auto Release via `workflow_dispatch` on oebb with an explicit `version`, confirming
   the changelog section, the commit, the tag on that commit, and a successful `release.yml` run.
3. Repeat for geosphere.

Both repos' changelogs were reconstructed against PyPI upload history on 2026-07-29 (geosphere 0.3.0,
oebb 0.2.0), so this starts from an accurate baseline.
