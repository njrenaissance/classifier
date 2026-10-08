---
name: issue-decomposition
description: Use this skill when the Sequence step breaks an approved spec (already on `main` after Bootstrap) down into GitHub issues. Covers what "independently buildable" means in practice and the content each created GitHub issue must carry. Trigger this whenever decomposing a feature into units of work, not only when the human says "issues" or "tickets."
allowed-tools: Read Glob Bash(gh issue list:*) Bash(gh issue create:*)
---

# Issue Decomposition

## When this applies

This skill applies during **Outer · Sequence** — a Claude Code session that
runs *after* Bootstrap, working against the bootstrapped repository. By then
the approved spec is on `main` as `spec/SPEC.md`, and the repo already has
its full project structure, CI, and issue templates in place.

This is **not** part of Plan, and it is **not** before Bootstrap — there is
no repo to decompose against until Bootstrap has run. The session's only
job is to read `spec/SPEC.md` (and any `spec/adrs/*.md`) and decompose it
into independently buildable GitHub issues.

## Before you start

1. Read `spec/SPEC.md`. If its `Status` is not `approved`, stop and tell the
   human — decomposing a draft just produces issues that get rewritten.
2. Read any `spec/adrs/*.md`; they constrain how issues should be cut.
3. Run `gh issue list --state all` and note what already exists. Never create
   a duplicate of an existing issue; if the spec changed since, say so and
   propose only the delta.

## What "independently buildable" actually means

An issue is decomposed finely enough when you can write its acceptance
criteria without referencing another issue's implementation details.
Use this as a direct test, not a vibe check:

> Read the issue body back to yourself. Does understanding it require
> knowing *how* another issue will be built — not just *that* it
> exists? If yes, split further or resequence.

It's fine for issue B to mention issue A's existence ("assumes the
`Token` type from issue #1 exists") — the human works issues in a sensible
order. It's not fine for issue B's acceptance criteria to be unwritable
without knowing issue A's internal design.

## Prefer smaller over larger

Each issue becomes one Inner Loop run: a plan the human approves, tests,
a build, and a PR someone has to read. A too-large issue produces an
unwieldy plan and an unreviewable diff, and gets split mid-flight. When in
doubt, split — a too-small issue costs a little coordination overhead.

**Signal an issue is too large:** its Done criteria would span multiple
unrelated behaviors, or its implementation would plausibly touch more than
a handful of files across different concerns.

**Signal an issue is appropriately sized:** you could describe its
acceptance criteria in a handful of testable statements (see the
spec-authoring skill's "testable vs. vague" guidance — the same bar
applies to issue-level Done criteria as project-level ones), and doing
so doesn't require describing another issue's internals.

## Propose the list, then create

Sequence is human-in-the-loop. Before running `gh issue create`, show the
human the full proposed list: a title and one-line scope per issue, plus a
coverage table mapping **every Done criterion in `SPEC.md` to the issue(s)
that satisfy it**. A criterion with no issue is a gap; an issue that maps to
no criterion is scope creep — fix either before proceeding. Create the
issues only after the human approves the list.

## What this step does NOT do

No dependency graph, no build order, no parallel grouping, and no
`spec/build-order.md`. Issues are worked one Inner Loop run at a time, in
whatever order the human picks. This step only opens issues — no code
changes, so no PR.

## Issue content and format

Create each unit of work in the repository's **GitHub Issues tracker** via
`gh issue create` — not as a file in the working tree. There is no
`issues/` folder; the only output of this step is new entries in the
repo's Issues list.

**Fill in the installed `feature_request` template — don't invent an issue
shape.** Read `.github/ISSUE_TEMPLATE/feature_request.md` in the repo and
populate every section from the spec, reading the template live rather than
assuming fields (the same way Bootstrap reads `cookiecutter.json`):
**What do you want to add**, **Why** (cite the spec's Purpose or the Done
criterion it serves), **Acceptance criteria**, and **Out of scope** (name the
adjacent work that belongs to a different issue, so the Inner Loop doesn't
wander). It sets the `feat:`
title prefix and the `enhancement` label. Its **Acceptance criteria**
section must be testable to the same bar as SPEC.md's Done criteria —
Inner · Write Tests authors the executable tests straight from it, so write
each criterion precisely enough that someone else could write a test from it
without asking you anything further. Reserve `bug_report` for defects, not
planned work.

```bash
gh issue create \
  --title "feat: <short, specific title>" \
  --label enhancement \
  --body-file <rendered feature_request body>
```
