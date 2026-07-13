# spec — Document Classifier

Status: draft

## Purpose

Classify document files into exactly one category from a user-defined category set, and emit a CSV mapping each filename to its assigned category and a confidence score.

## Inputs / Outputs

### Input
- **File types:** PDF, DOCX, DOC
- **Sources (both in v1):**
  - Local filesystem (a file or a directory of files)
  - SharePoint, via the Microsoft Graph API
- **Category definitions:** a Markdown file that defines the allowed categories, each with few-shot examples. This is the single source of the label set — categories are never hardcoded.

### Output
- A **CSV** with columns: `filename`, `category`, `confidence`.
- Written to the local filesystem. Richer output formats are out of initial scope.

## What we produce

A **CLI / batch job**: point it at a source (local path or SharePoint location) plus a category-definition Markdown file → it writes a CSV.
> Decision: [ADR-0003](adr/0003-cli-batch-interface.md) (CLI/batch vs library vs service).

## Where we persist

Output persisted as a **CSV file on the local filesystem**. No database in initial scope.
> Decision: [ADR-0004](adr/0004-csv-file-output.md) (file/CSV vs DB).

## Method

**LLM-based classification using `claude-haiku-4-5`.** For each document: extract its text, then classify it against the category definitions + few-shot examples drawn from the Markdown file. The model assigns exactly one category from the defined set **or the reserved `unknown` category** when nothing fits, plus a **confidence** value. Low-confidence / `unknown` rows surface uncertainty for human review rather than being silently forced into a real category.
> Decisions: [ADR-0001](adr/0001-llm-based-classification.md) (LLM vs rules vs classical ML); [ADR-0002](adr/0002-model-haiku-4-5.md) (model = Haiku 4.5).

**Confidence via self-consistency.** Logprobs are unavailable on the Anthropic Messages API (Haiku 4.5), so confidence is derived by **classifying each document N times and using the agreement rate as the score** (e.g. 5/5 identical → 1.0, 3/5 → 0.6). The winning category is the modal (most-frequent) label across the N runs; ties or a modal count at/below a threshold resolve to `unknown`.
> Decision: [ADR-0005](adr/0005-confidence-self-consistency.md) — N-run self-consistency (default N configurable, e.g. 5) + reserved `unknown` category. Logprob-based scoring was rejected because the Anthropic API does not expose logprobs. Open tunables (N, the tie/threshold rule) are pinned during Phase 1.

## Constraints / Rules

- **Single-label:** each file is assigned exactly one category (categories are mutually exclusive). `confidence` is metadata, not a second label.
- **Reserved `unknown` category:** there is always an `unknown` bucket for documents that fit no defined category; the model is never forced to guess a real category. Uncertainty is expressed via both `unknown` and `confidence`.
- **Config-driven label set:** the *real* categories come from the Markdown file, not code (`unknown` is the one built-in). A real category not defined in the Markdown file is never emitted.
- Supported input types are exactly PDF, DOCX, DOC; anything else is handled explicitly (see done criteria).

## Text extraction

Per-format Python libraries (PDF, DOCX, legacy `.doc`).
> Decision: [ADR-0006](adr/0006-text-extraction-per-format-libs.md). **Sub-item still open:** the concrete legacy-`.doc` handler is unresolved (see the ADR) — pin during Phase 1.

## SharePoint access

Microsoft Graph, app-only (client-credentials) auth.
> Decision: [ADR-0007](adr/0007-sharepoint-app-only-auth.md). **Specifics still open:** Azure AD app registration + exact Graph scopes — pin during Phase 1; may warrant its own build issue.

## Scale

Target: **< 100 files per run.** Sequential processing is acceptable — no batching, concurrency, or resumability required in v1. (Revisit if volumes grow — would warrant its own ADR.)

## Open questions (to resolve during Phase 1 iteration)

1. **Prompt shape / structured output** — how the category Markdown (definitions + few-shot examples) is rendered into the Haiku prompt, and how `category` + `confidence` are reliably returned. Recommendation: structured output / a tool call so parsing is deterministic. **Not yet decided — needs its own ADR.**
2. **Deferred implementation specifics** (decisions made, details to pin in Phase 1): the concrete `.doc` handler ([ADR-0006](adr/0006-text-extraction-per-format-libs.md)); Graph app registration + scopes ([ADR-0007](adr/0007-sharepoint-app-only-auth.md)).

## Acceptance criteria

Product-level, observable behaviors that define done. These are *what to verify*; the executable TDD unit tests that assert them are authored per issue in the build loop and live in `tests/` — not here (see `coding-workflow.md` → Spec Artifact → Acceptance criteria vs. executable tests).

- Given a local PDF (and a DOCX, and a DOC) plus a category file, the tool produces a CSV row assigning the correct single category and a confidence value to each.
- Every file in a target source (local dir or SharePoint location) appears **exactly once** in the output CSV.
- An unsupported file type is handled explicitly (skipped-with-warning or errored — TBD), not silently mis-processed.
- The allowed categories are read from the Markdown file; a category not defined there is never emitted.
- SharePoint and local-filesystem sources produce the same CSV shape for equivalent files.
- (Criteria refined as issues are planned; each issue decomposes the relevant ones into concrete tests.)
