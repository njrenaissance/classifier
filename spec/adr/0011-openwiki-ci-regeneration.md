# ADR-0011 — OpenWiki regeneration via scheduled CI

Status: accepted

## Context

The project keeps an `openwiki/` folder of LLM-generated codebase docs that must
stay in step with `main` ([`.claude/standards/wiki.md`](../../.claude/standards/wiki.md)).
Until now, keeping it current has been a **manual, agent-discipline** job: the
coding agent was expected to run `openwiki code --update` and commit the
refreshed `openwiki/` in the same PR as the code change. That depends on the
agent remembering to do it every time, and in practice the `openwiki/` folder
was never generated at all.

An earlier revision of [issue #20](https://github.com/njrenaissance/classifier/issues/20)
was closed `not_planned` in favour of an agent-workflow step, having rejected a
*blocking, regenerate-and-diff CI gate* fed by *per-developer-machine API keys*.
OpenWiki's own shipped recommendation (`examples/openwiki-update.yml`) is
neither of those things: it is a **scheduled, non-blocking** workflow that
regenerates the wiki and opens a review PR, using a **single repo secret**. This
ADR re-evaluates the decision against that design.

Provider and model follow the precedent set by [ADR-0002](0002-model-haiku-4-5.md):
stay in the Anthropic/Claude family the app already uses.

## Decision

Adopt a scheduled GitHub Actions workflow,
[`.github/workflows/openwiki-update.yml`](../../.github/workflows/openwiki-update.yml),
as the **primary** mechanism for regenerating `openwiki/`:

- **Trigger:** daily `schedule` (`cron: "0 8 * * *"`) plus `workflow_dispatch`
  for manual runs.
- **Command:** installs the global `openwiki` CLI and runs
  `openwiki code --update --print` (`--update` bootstraps `openwiki/` on the
  first run; no `--init` needed in CI).
- **Provider / model:** `OPENWIKI_PROVIDER=anthropic` with
  `OPENWIKI_MODEL_ID` defaulting to **`claude-haiku-4-5`** — the cheapest Claude
  model, matching [ADR-0002], appropriate because output is human-reviewed
  before merge. The model is a repo **variable** so it is tunable without
  editing the workflow.
- **Delivery:** `peter-evans/create-pull-request` opens/updates a single rolling
  review PR from branch `openwiki/update` (commit/title `docs: update OpenWiki`).
  **No auto-merge** — the PR awaits normal human review.
- **Staleness marker:** the workflow writes a top-level `openwiki/GENERATED.md`
  after each run recording the UTC timestamp, source commit SHA, and model, so
  any reader can judge how current the wiki is.

CI is now the **primary** path; the agent's regenerate-before-commit step is
**relaxed to optional**. `openwiki/` may therefore lag `main` by up to a day and
land in its own PR — accepted, and surfaced via `openwiki/GENERATED.md`.

## Alternatives

- **Agent-workflow step only (the prior decision)** — keeps `openwiki/` in the
  *same* PR as the code change, but depends on the agent remembering every time;
  in practice the folder was never produced. Rejected as the primary mechanism.
- **CI as a backstop (agent primary + scheduled safety net)** — lower-risk
  framing that leaves the agent step intact and uses CI only to catch drift.
  Considered and not chosen: the maintainer opted for CI as the single, reliable
  primary rather than continuing to lean on agent discipline.
- **A blocking regenerate-and-diff CI gate** — the design the earlier revision
  rejected; flaky and blocks commits on a non-deterministic LLM diff. Still
  rejected; the adopted workflow is non-blocking and never fails a build.
- **A stronger model (Sonnet 5) as the default** — better long-form prose at
  ~3× cost. Rejected as the default because output is human-reviewed and
  `OPENWIKI_MODEL_ID` can be raised later without code changes.

## Tradeoffs

- **Gain:** `openwiki/` stays current without relying on agent discipline; a
  single `ANTHROPIC_API_KEY` repo secret (no key on any dev machine); the run is
  non-blocking and never gates a build; one rolling, human-reviewed PR rather
  than daily churn.
- **Give up:** one paid Anthropic run per day, which can open a low-value PR when
  nothing meaningful changed; docs can trail `main` by up to a day and arrive in
  a separate PR rather than alongside the code that prompted them.

## Consequences

- Two pieces of repo configuration are required (GitHub settings, not the repo
  tree): an `ANTHROPIC_API_KEY` **secret**, and an `OPENWIKI_MODEL_ID`
  **variable** (`claude-haiku-4-5`; the workflow falls back to it if unset). The
  repo setting "Allow GitHub Actions to create and approve pull requests" must be
  enabled for `create-pull-request` to open the PR with the default token.
- `openwiki` stays a per-machine global CLI — it is never added to
  `pyproject.toml` / `uv.lock`. `openwiki/` remains generated output, never
  hand-edited; the CI-written `openwiki/GENERATED.md` is the sole exception and
  is owned by the workflow.
- Because the docs PR touches only markdown, `ci.yml` (which sets
  `paths-ignore: ["**.md", ...]`) does not run its code checks on it, and PRs
  opened by the default token do not re-trigger `pull_request` workflows; the
  gate on this PR is human review, matching the non-blocking intent.
- The Deliver-phase agent wiki-regen step in
  [`coding-workflow.md`](../../coding-workflow.md) is now redundant and relaxed
  to optional; the broader agent-loop restructure is tracked in
  [issue #21](https://github.com/njrenaissance/classifier/issues/21).
- Raising `OPENWIKI_MODEL_ID` to a stronger Claude model needs no superseding
  ADR (it is a repo variable); a change to the model *policy* would be recorded
  as one, per [ADR-0002].
