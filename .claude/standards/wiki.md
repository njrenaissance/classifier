# Codebase Wiki

- Every project keeps an `openwiki/` folder of generated codebase
  documentation, produced by OpenWiki — an LLM-powered tool that writes
  structured Markdown describing the code (module boundaries, architecture,
  workflows). Read the relevant wiki pages for context at the start of a
  task instead of re-deriving how the codebase fits together from scratch.
- `openwiki/` is **generated output — never hand-edit it.** The source of
  truth is always the code. If a wiki page is wrong, the fix is in the code
  (or the code's own comments/docstrings), followed by regenerating the
  wiki — not an edit to the Markdown, which the next regeneration would
  silently overwrite.
- **Regenerating in the same PR is optional.** A scheduled CI workflow is now
  the primary way `openwiki/` is kept current (see the CI bullet below), so you
  are no longer required to regenerate and commit `openwiki/` alongside every
  code change. Doing it in the same PR — `openwiki code --update`, then staging
  the result — is still welcome when you want the docs to land immediately, but
  it is no longer part of a change's definition of done.
- **The wiki can lag the code.** Under CI-primary regeneration the wiki may
  trail `main` by up to a day. Read the relevant wiki pages for context at the
  start of a task, but check the top-level `openwiki/GENERATED.md` (last
  regenerated timestamp + source commit) to judge how current they are, and
  whenever a wiki page disagrees with the code, **the code is the source of
  truth** — sanity-check anything that looks stale against the current source
  before acting on it.
- Regenerating calls a paid LLM provider and needs one-time setup
  (`npm install -g openwiki`, then `openwiki auth <provider>`). Install and
  auth instructions live in the project `README`; `openwiki` is a per-machine
  global CLI, **not** a project dependency, and must never be added to
  `pyproject.toml`.
- Regeneration is automated in CI. `.github/workflows/openwiki-update.yml`
  runs `openwiki code --update` on a daily schedule (and on demand via
  `workflow_dispatch`) on the Anthropic provider, opening a single rolling
  review PR from the `openwiki/update` branch — non-blocking, no auto-merge,
  gated by human review. See [ADR-0011](../../spec/adr/0011-openwiki-ci-regeneration.md)
  for the decision and its tradeoffs.
