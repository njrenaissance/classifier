# Agent-Based Coding Workflow

## Principles

- **Determinism first.** Every step has a defined input, process, and exit condition. Agents operate within guardrails, not open loops.
- **Minimize HITL.** Human-in-the-loop is the most expensive step. Only require it where human judgment is irreplaceable — outer Plan approval, inner Plan approval, and inner Review.
- **AI fixes its own mess.** Pre-commit hooks and CI/CD must pass before a human ever sees the work. If they fail, the agent fixes and retries.
- **Context-free code review.** Code review subagents have no context from the build step. They see only the diff — same as a fresh human reviewer would.
- **Gradual tool trust.** Tools start locked. A tool whitelist is expanded over time as confidence is established. Agents cannot use tools outside the whitelist without explicit approval.
- **Right model for the task.** Model selection is explicit per step to avoid overspending on cheap tasks and underspending on critical ones.
- **Remote execution.** Automated steps (inner Build, inner Validate) run in cloud-isolated agent sandboxes via the Claude Agent SDK. The human's machine is never a dependency for these steps.
- **Async by default.** The human is notified only when a HITL gate is reached or a structured error halts the pipeline. No polling, no babysitting.

---

## The Workflow

The process is two nested loops. The **outer loop** operates at the project/feature level — it plans, builds, and ships a whole feature. The **inner loop** operates per planned issue — it runs once for every unit of work the outer loop decomposed, repeating until all planned issues are delivered.

```text
Outer:  Scaffold → Plan (HITL) → Build → Deliver
                              └─ Build invokes, per planned issue: ─┐
Inner:                          Plan (HITL) → Build → Validate → Review (HITL)
```

### Outer Loop — project / feature level

#### Outer · Scaffold `[Automated]`

**Goal:** Create a deterministic project structure if this is a new project.

1. Agent invokes the `scaffold-project` skill, which calls Cookiecutter with a locked template.
2. No agent decisions are made here — template is deterministic.
3. Pre-commit hooks are installed automatically as part of the scaffold.

**Model:** None / minimal (tool invocation only).
**Exit condition:** Project directory created, pre-commit installed, CI config in place. A no-op for an existing project.

---

#### Outer · Plan `[HITL]`

**Goal:** Determine what can be built in parallel and what must be sequenced, and agree on the product-level plan before any issue enters Build.

1. A **context-free sequencing subagent** receives the full list of open issues labeled `ready-to-plan` or `ready-to-build`.
2. It analyzes each issue for dependencies — shared modules, data model changes, API contracts, migration requirements — and produces:
   - A **dependency graph** (which issues block which)
   - A **parallel execution groups** list (issues with no interdependencies that can run simultaneously)
   - A **recommended build order** for sequenced issues
3. In parallel, a reasoning-model agent reads the foundational feature description and produces (or updates) the canonical **`spec/spec.md`** (see "## Spec Artifact" below) — the product-level plan and decomposition into independently buildable issues.
4. Output (dependency graph, parallel groups, build order, `spec.md`) is written back to GitHub (e.g., as issue comments or labels) and to the repo, and used to schedule outer Build's invocations of the inner loop.
5. Human explicitly approves the product spec and the issue decomposition/build order. This is the **outer HITL gate**.
6. This step reruns whenever new issues are added to the queue.

**Model:** Reasoning-class — dependency inference and product-level planning require understanding code relationships and risk, not just surface-level issue text.
**Exit condition:** Dependency graph and build order established; `spec.md` approved (`Status: approved`) and committed.

---

#### Outer · Build `[Automated]`

**Goal:** Deliver every planned issue.

1. Outer Build invokes the **inner loop once per planned issue**, honoring the dependency graph and parallel execution groups established in outer Plan — sequenced issues run one at a time in build order, issues in the same parallel group run simultaneously.
2. Outer Build iterates until every planned issue has completed its inner loop (merged via inner Review).
3. Once all planned issues are delivered, control passes to outer Deliver.

**Model:** None (orchestration only — the work happens inside the inner loop it invokes).
**Exit condition:** All planned issues have completed the inner loop and merged.

---

#### Outer · Deliver `[Automated]`

**Goal:** Integrate the completed issues and ship.

1. Agent integrates the merged issues (if not already integrated incrementally) and runs an end-to-end / acceptance pass against the product `spec/spec.md`'s acceptance criteria.
2. Agent regenerates the codebase wiki (`openwiki code --update`, see "## Codebase Wiki" below) and commits the refreshed `openwiki/`.
3. Agent ships: creates a release or merges the integrated work to `main`.
4. If the acceptance pass fails, the agent raises a structured error and halts — a failure here means one or more issues did not actually satisfy the product spec despite passing their own inner Review, and is treated as a planning or integration gap for the human to triage.

**Model:** Light / fast model for the acceptance pass; minimal for the wiki regenerate and release/merge mechanics.
**Exit condition:** End-to-end acceptance pass green against the product spec, wiki refreshed and committed, release/merge to `main` complete.

---

### Inner Loop — per planned issue

Runs once for each issue outer Build hands it, in the order (sequential or parallel per outer Plan's groups) outer Build assigns.

#### Inner · Plan `[HITL]`

**Goal:** Agree on what to build and how to verify it before writing any production code, for this one issue.

1. Human opens (or outer Plan has already opened) an issue describing the feature or fix.
2. Agent (reasoning model) reads the issue and the product `spec/spec.md`, and produces this issue's plan — the relevant slice of implementation detail (purpose, inputs/outputs, what we produce, where we persist, method) plus **acceptance criteria** — observable behaviors that define done for this issue. These are *what to verify*, not the test code itself.
3. The agent also authors the **executable TDD unit tests** that assert those acceptance criteria. The tests live in `tests/` (version-controlled with the code), **not** inside `spec.md` — the spec carries the criteria, the loop carries the tests (see "## Spec Artifact → Acceptance criteria vs. executable tests"). Human reviews and iterates on both the plan and the tests until satisfied.
4. **Turn limit: 5 rounds of iteration.** If the plan has not been approved after 5 rounds, the issue is flagged as too large or too ambiguous. The agent halts planning, proposes how to split the issue into smaller, independently buildable sub-issues, and the human approves the split before any sub-issue re-enters outer Plan for re-sequencing.
5. Human explicitly approves. This is the **inner HITL gate** for this issue.

**Model:** Reasoning-class (e.g., claude-opus or equivalent) — this is where correctness matters most.
**Exit condition:** This issue's plan approved and agreed unit tests committed. If turn limit hit: human approval of issue split, sub-issues created in GitHub, each re-enters outer Plan.

---

#### Inner · Build `[Automated]`

**Goal:** Write code that passes the approved unit tests.

1. Agent implements code against the approved plan and unit tests.
2. Agent may only use **whitelisted tools.** The whitelist is maintained separately and expanded deliberately over time.
3. Agent runs the approved unit tests locally in a loop until they pass.
4. Agent does not ask for human input. If it cannot proceed, it raises a structured error and halts.

**Model:** Code-generation class (e.g., claude-sonnet or equivalent) — balance of quality and cost.
**Exit condition:** All approved unit tests pass locally.

---

#### Inner · Validate `[Automated]`

**Goal:** Ensure the code meets quality standards before any human sees it.

1. Pre-commit hooks run: linting, formatting, unit tests.
2. Agent pushes to a branch and CI/CD pipeline runs.
3. If any check fails, the agent diagnoses and fixes — no human involvement.
4. Retry loop continues until all checks pass or the retry limit is hit.
5. **Retry limit: 3 attempts.** After 3 failed attempts, the agent halts, preserves full context (error logs, last diff, attempted fixes), and sends an async notification. Rationale: a 4th attempt is unlikely to produce a different result, and continued retries waste cost without improving recoverability. The limit is tunable as failure patterns become understood.

**Model:** Light / fast model for diagnosis; same code-gen model for fixes.
**Exit condition:** All pre-commit hooks and CI/CD checks pass cleanly.

---

#### Inner · Review `[Automated → HITL]`

**Goal:** Catch logic errors, security issues, and design problems that automated checks miss.

1. Agent opens a PR.
2. A **context-free subagent** (no knowledge of the build conversation) reviews the diff and leaves structured comments — logic, security, edge cases, adherence to plan.
3. Human reviews the PR and the subagent's comments.
4. Human approves or requests changes.
   - If changes requested → back to inner Build, plan amendment optional.
   - If approved → merge. This issue's inner loop is complete; outer Build advances to the next planned issue (or, once all are done, to outer Deliver).

**Model:** Reasoning-class for the review subagent — this is a critical thinking task.
**Exit condition:** Human approval and merge.

---

## Spec Artifact (`spec/spec.md`)

Outer Plan leaves one durable artifact that the entire build process references: a canonical **`spec.md`** under a dedicated **`spec/`** folder (`spec/spec.md`), alongside the project's ADRs (`spec/adr/` — see "## Architecture Decision Records" below). It is version-controlled alongside the code, so every step reads the exact spec that matches the commit it is working on — unlike a GitHub issue, which is a conversation surface that can drift from `main`.

### Why in-repo rather than the issue

- **Diffable** — changes to the spec show up in PRs like any other change.
- **Travels with the checkout** — a remote build agent has the spec without calling the GitHub API.
- **Versioned with the commit** — inner Build builds against the spec as it existed at that commit; inner Review reviews the diff against it.

The GitHub issue still exists as the HITL discussion surface and links to `spec.md`; the file is the source of truth.

### Structure

| Field | Contents |
|---|---|
| Status | `draft` while iterating, `approved` once the human signs off |
| Purpose | One sentence: what problem this build solves |
| Inputs / Outputs | The contract — what comes in, what comes out |
| What we produce | library \| CLI \| service \| batch |
| Where we persist | stateless \| file \| DB |
| Method | rules \| classical ML \| LLM |
| Done criteria | Observable behaviors the TDD unit tests assert |

### Who reads it

- **Outer Plan** — to infer what a spec depends on, and as the artifact being authored and approved.
- **Inner Plan** — the product-level contract each issue's own plan is scoped against.
- **Inner Build** — the contract the code must satisfy.
- **Inner Review** — the spec the diff is checked against.

### Who writes it

The outer Plan agent authors `spec.md`; the human iterates and approves. Once approved (`Status: approved`) it is committed and treated as fixed for that build — changes to an approved spec go back through outer Plan.

### Acceptance criteria vs. executable tests

The spec and the tests sit at **two different altitudes** — keep them separate:

- **`spec.md` carries acceptance criteria** — observable, product-level behaviors that define done ("every file appears exactly once in the CSV"; "a category not defined in the Markdown file is never emitted"). These are *what to verify*.
- **The inner loop carries the executable TDD unit tests** — concrete test code that asserts those criteria, authored per issue in inner Plan and living in `tests/`. These are *how it's verified*.

Do **not** freeze concrete test cases into `spec.md`: a product spec shouldn't churn every time an issue-level test is added, and a hundred test cases don't belong in a design doc. The tests trace back to the spec's acceptance criteria but are decomposed to each buildable issue's scope. (Corollary: a foundational/product spec is authored once and *spawns issues*; each issue's own inner Plan then produces that issue's plan + its executable tests.)

### One living document — no initial snapshot

There is **one** `spec/spec.md` and it is **living**: it always reflects the current intended state of the build, not the state at some past moment. Do **not** keep a frozen `spec-initial.md` or otherwise preserve the starting point as a separate file — the starting point is captured for free by version control (the commit where `Status` flips to `approved` *is* the initial spec; `git show <commit>:spec/spec.md` reconstructs it exactly).

The reason this is safe — and why the spec doesn't need to carry its own history — is that the immutable "how we got here" record lives in the **ADRs**, not the spec:

- **`spec/spec.md`** — living *current state*: "what we're building, now." Mutable by design.
- **`spec/adr/`** — append-only *decision log*: "why we chose this, what we rejected." Superseded ADRs stay in place marked `superseded`; the trail is never lost.

So making the spec living loses nothing — the decision history is in the ADR trail plus git. If the spec tried to also carry that history, it would fill with struck-through old decisions, which is exactly what ADRs exist to keep out of it.

**Living ≠ silently mutated.** The `Status:` line is the guard: editing an `approved` spec is a real event — it re-enters outer Plan, and if it reverses a tradeoff-bearing decision it gets a **superseding ADR**. Routine iteration while still `draft` is free.

### Scope

One `spec/spec.md` per build. If a project needs several concurrent specs (outer Plan's parallel-issue model taken to its limit), graduate to numbered spec files under the same folder (`spec/0001-<slug>.md`) — **deferred until a project actually needs it.**

---

## Architecture Decision Records (`spec/adr/`)

**Every decision made with meaningful tradeoffs gets its own ADR Markdown file** under `spec/adr/` (`spec/adr/0001-<slug>.md`), alongside `spec/spec.md`. This is a hard rule, not a suggestion: library choices, storage backend, interface shape, classification method (rules vs classical ML vs LLM), source abstractions, output format — any choice with real alternatives is recorded so later steps and future readers don't re-litigate settled decisions or lose the rationale.

### When to write one

Whenever a choice has genuine alternatives and tradeoffs. A forced or deterministic choice (e.g. a locked template default) needs no ADR; a judgment call does. Decisions captured in `spec.md` that carry tradeoffs (e.g. "what we produce", "where we persist", "method") each reference their ADR.

### Structure

| Field | Contents |
|---|---|
| Status | `proposed` \| `accepted` \| `superseded (by ADR-NNNN)` |
| Context | The problem and the forces at play |
| Decision | What we chose |
| Alternatives | Options considered and why they were not chosen |
| Tradeoffs | What we gain and what we give up |
| Consequences | Follow-on effects and new constraints |

ADRs are **append-only**: a decision that changes is not edited in place — a new ADR supersedes the old one, and the old one is marked `superseded`.

---

## Cross-Cutting Concerns

Cross-cutting concerns (configuration, telemetry, logging, error handling, security, etc.) are **infrastructure-level standards**, not feature work. They are handled at two levels:

### 0. Project Design Prompt (Scaffold Time)

Before any code is written, outer Scaffold asks a small set of design questions that determine which cross-cutting concerns are active for this project. These are Cookiecutter prompt variables — answered once, baked into the project forever.

The scaffold prompt asks four independent `no`/`yes` toggles — each concern opts in on its own rather than being bundled into a fixed tier:

| Toggle | What it adds when `yes` |
|---|---|
| `app_config` | Application settings / config loading (`pydantic-settings`) |
| `structured_logging` | Structured logging (`structlog`) |
| `telemetry` | OpenTelemetry traces and metrics |
| `security` | Security scanning (`bandit`) plus input validation, secrets handling, and dependency-hygiene standards |

These selections determine:
- Which dependencies are added to the project
- Which standards documents are imported into `CLAUDE.md`
- Which `foundational` issues are auto-created in GitHub
- What the review subagent checks for in inner Review

All decisions are recorded in `CLAUDE.md` (see its `## Profile` section) so every agent knows the project's active concerns without asking.

### 1. Standards Documents (Template → `CLAUDE.md`)

Every project template ships with a set of standards documents covering each cross-cutting concern. These are imported into `CLAUDE.md` so every agent reads them automatically on every invocation — no explicit referencing required.

The template ships standards documents for every concern. Some are imported into `CLAUDE.md` unconditionally; the rest are gated on the matching toggle:

| File | Imported |
|---|---|
| `.claude/standards/git-workflow.md` | always |
| `.claude/standards/wiki.md` | always |
| `.claude/standards/testing.md` | always |
| `.claude/standards/error-handling.md` | always |
| `.claude/standards/logging.md` | always (self-gates `structlog` vs stdlib on `structured_logging`) |
| `.claude/standards/configuration.md` | when `app_config` is `yes` |
| `.claude/standards/telemetry.md` (OTEL) | when `telemetry` is `yes` |
| `.claude/standards/security.md` | when `security` is `yes` |

Each standards document is prescriptive in two ways:

1. **How** — patterns, conventions, and rules agents must follow
2. **What** — the specific Python libraries approved for that concern (no agent should substitute its own choice)

The Cookiecutter template uses conditional logic to inject the correct dependencies into `pyproject.toml` (or `requirements.txt`) based on the toggles selected at scaffold time. An agent never decides which logging or telemetry library to use — that decision is made once in the standard and enforced by the template.

Example standard library assignments (subject to revision):

| Concern | Approved libraries |
|---|---|
| App settings / config | `pydantic-settings` (with `python-dotenv` for `.env` support, used transitively) |
| Structured logging | `structlog` |
| OpenTelemetry | `opentelemetry-sdk`, `opentelemetry-api`, relevant exporters |
| Security | `bandit` (static analysis) |
| Testing | `pytest`, `pytest-cov`, `pytest-mock` |

These documents are **prescriptive** — they define how things must be done across all projects. They evolve slowly and intentionally; changes to a standard (including library upgrades) are made in the template and consciously adopted by new projects.

### 2. Foundational Issues

Cross-cutting concerns that require actual implementation work (e.g., "Set up telemetry framework", "Define config loading strategy") are created as GitHub issues labeled `foundational`. Outer Plan's sequencing subagent always schedules `foundational` issues before any feature work — nothing builds on top of infrastructure that doesn't exist yet.

---

## Codebase Wiki (`openwiki/`)

Every project maintains an `openwiki/` folder as the shared memory for all agents. It is generated by **OpenWiki** — an LLM-powered CLI (`npm install -g openwiki`) that produces structured Markdown documentation from the codebase. The `openwiki/` content is **generated output — never hand-edited**; the source of truth is always the code itself. The agent-facing rule lives in the generated project's `.claude/standards/wiki.md`; per-machine install and usage are documented in its `README`.

### Why OpenWiki

Evaluated against the Gideon codebase, OpenWiki produced output that:

- Correctly identified module boundaries, key abstractions, and security-critical components
- Generated a `source-map.md` mapping every file to its purpose and related wiki pages — directly usable by the outer Plan sequencing agent
- Produced workflow and architecture docs specific enough to guide inner Plan planning and inner Build building
- Regenerates from a single command (`openwiki code --update`), so refreshing it is one deterministic step rather than a manual doc-writing chore

This is better than agents maintaining wiki docs manually: it's deterministic, consistent, and always reflects the actual code.

### Structure

OpenWiki generates a consistent folder structure:

| Path | Contents |
|---|---|
| `openwiki/source-map.md` | Maps every file to its purpose and related wiki pages; includes "I want to..." task index |
| `openwiki/architecture/overview.md` | Service layers, tech stack, key abstractions, security invariants |
| `openwiki/architecture/flows.md` | Data flow diagrams and walkthroughs |
| `openwiki/architecture/permission-model.md` | Role definitions, access control rules |
| `openwiki/workflows/` | Per-workflow docs (auth, ingestion, RAG query, etc.) |
| `openwiki/operations/` | Deployment, configuration, background jobs |
| `openwiki/testing/overview.md` | Test structure, fixtures, coverage approach |

### Who Reads It

Every agent reads the relevant wiki documents at the start of its invocation:

- **Outer Plan (sequencing)** — `source-map.md` + `architecture/overview.md` to infer issue interdependencies
- **Inner Plan** — full wiki for context before proposing a plan and tests
- **Inner Build** — `source-map.md` + relevant workflow doc to write consistent code
- **Inner Review** — `architecture/overview.md` to assess adherence to established patterns and security invariants

### Who Writes It

OpenWiki is the only writer. Regeneration happens at two points: incrementally, before committing any change that alters what the wiki describes (the developer or agent runs `openwiki code --update` and commits the refreshed `openwiki/` in the same pull request); and definitively, as part of outer **Deliver**, which refreshes and commits `openwiki/` as part of shipping the integrated feature. Nobody hand-edits `openwiki/` — corrections to the wiki are corrections to the code, followed by a regenerate. Automating this in CI (a workflow that regenerates the wiki and opens a PR on merge to main) is a **deferred future goal**, not yet wired up.

### Setup

The `openwiki` CLI is a global npm tool (`npm install -g openwiki`) that developers install once per machine, then authenticate once (`openwiki auth <provider>`); it is not a project dependency and does not appear in `pyproject.toml`. First run in a fresh repo uses `openwiki code --init`; subsequent refreshes use `openwiki code --update`. The template documents this in the generated project's `README` (`## Wiki` section) and enforces the regenerate-before-commit rule via `.claude/standards/wiki.md`. (A pre-configured CI workflow was considered and deferred — see "Who Writes It" above.)

---

## Tool Whitelist Policy

Tools available to agents are locked by default. A tool is added to the whitelist only after:

1. It has been used successfully in a supervised (HITL) context.
2. There is confidence its failure modes are understood and recoverable.

The whitelist is stored per project in `.claude/settings.json` and versioned with the code. This means tool trust is explicit, auditable, and scoped.

### Two-tier whitelist

**Template-level (Cookiecutter default):** The project template ships with a baseline set of pre-approved tools in `.claude/settings.json` — tools that have proven safe and useful across all projects. When a new project is scaffolded, it inherits this baseline automatically.

**Project-level (per-project extension):** Individual projects can add tools to the whitelist beyond the template baseline, stored in the same `.claude/settings.json`. These are specific to that project and do not affect others.

### Promoting a tool to the template

When a project-level tool proves reliable across multiple projects, it can be promoted to the template baseline:

1. Update the Cookiecutter template's `.claude/settings.json` to include the tool.
2. Existing projects retain their own settings and are not auto-updated — promotion only affects new projects going forward.

This creates a ratchet: tools earn trust in individual projects first, then graduate to the shared template over time.

---

## Model Selection Guide

| Step | Task complexity | Recommended tier |
|---|---|---|
| Outer Plan — sequencing subagent | High — dependency inference | Reasoning-class |
| Outer Plan — product spec | High — reasoning, ambiguity | Reasoning-class |
| Outer Scaffold | Trivial — tool invocation | Minimal / none |
| Outer Build | None — orchestration only | Minimal / none |
| Outer Deliver | Low–Medium — acceptance pass, wiki regen, release mechanics | Fast / cheap |
| Inner Plan | High — reasoning, ambiguity | Reasoning-class |
| Inner Build | Medium — code generation | Mid-tier |
| Inner Validate (diagnosis) | Low — pattern matching | Fast / cheap |
| Inner Review subagent | High — critical analysis | Reasoning-class |

---

## Remote Execution & Async Notification

### Execution Model

Inner Build and inner Validate run as remote agents using the **Claude Agent SDK with `isolation: "remote"`**. This means:

- The agent runs in a cloud sandbox — no dependency on the human's local machine.
- The human can trigger a session from anywhere (laptop, phone, etc.) and walk away.
- Each step is a discrete agent invocation with a defined input and exit condition, so failures are isolated and restartable.

Outer Plan, inner Plan, and inner Review require HITL and run locally or via a lightweight interface where the human can respond.

### Triggering

A coding session can be triggered from anywhere — no local machine required:

- **GitHub-driven** — human creates or updates an issue, or applies a label (e.g., `ready-to-plan` or `ready-to-build`). A GitHub webhook fires and kicks off the appropriate step.
- **Manual remote** — human sends a command from any device (e.g., a lightweight UI, Slack command, or CLI against an API endpoint) to trigger a specific step.
- **Post-approval continuation** — after the human approves inner Plan for an issue, the pipeline automatically advances to inner Build without any additional trigger. Likewise, after outer Plan is approved, outer Build automatically begins invoking the inner loop per the established build order.

The intent is that the human's only required interactions are: (1) create/label a GitHub issue and (2) respond to async notifications at HITL gates.

### Notification Events

The human is notified asynchronously at these points only:

| Event | Notification | Action required |
|---|---|---|
| Outer Plan ready for review | Agent posts product spec + issue decomposition/build order | Human reviews and approves or iterates |
| Inner Plan ready for review | Agent posts per-issue plan + unit tests | Human reviews and approves or iterates |
| Inner Build/Validate error (retry limit hit) | Agent posts structured error report | Human investigates and decides next step |
| Inner Review PR ready | Agent posts PR + review subagent comments | Human reviews and approves or requests changes |
| Outer Deliver error (acceptance pass failed) | Agent posts structured error report | Human investigates and decides next step |

Notification channel: **GitHub comments**. The agent posts a structured comment on the relevant issue or PR at each HITL gate. This keeps all context in one place and requires no extra infrastructure. A consistent comment format (e.g., a `[HITL]` prefix) distinguishes action-required notifications from regular activity. Human responds by commenting or applying a label directly in GitHub.
