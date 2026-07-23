# spec — Document Classifier

Status: approved · v2 (cloud pipeline)

## Purpose

Classify document files into exactly one category from a user-defined category set, recording each file's assigned category and a confidence score.

**v1 → v2.** v1 is a local CLI batch job that reads local files and writes a CSV ([ADR-0003](adr/0003-cli-batch-interface.md), [ADR-0004](adr/0004-csv-file-output.md)); it remains the local-development interface. v2 adds the production deployment: an incremental, resumable **SharePoint → PostgreSQL** pipeline running as two Azure Container Apps jobs, decoupled by an Azure queue ([ADR-0012](adr/0012-cloud-two-job-pipeline.md)–[ADR-0015](adr/0015-graph-authenticated-download.md)). The classification method (extract → self-consistency → verdict) is identical in both.

## Inputs / Outputs

### Input
- **File types:** PDF, DOCX, DOC
- **Sources:**
  - Local filesystem (a file or a directory of files) — local dev
  - SharePoint, via the Microsoft Graph API — production ([ADR-0012](adr/0012-cloud-two-job-pipeline.md))
- **Category definitions:** a Markdown file that defines the allowed categories, each with few-shot examples. This is the single source of the label set — categories are never hardcoded.

### Output
- Fields: `filename` (or file identity), `category`, `confidence`.
- **Local dev:** a **CSV** on the local filesystem. **Production:** rows in **PostgreSQL** ([ADR-0013](adr/0013-postgresql-state-store.md)).

## What we produce

**Local dev — a CLI / batch job**: point it at a local path plus a category-definition Markdown file → it writes a CSV.
> Decision: [ADR-0003](adr/0003-cli-batch-interface.md) (CLI/batch vs library vs service).

**Production — a two-job cloud pipeline** (one image, two entry points), decoupled by an Azure Queue:
- **Walker** (scheduled ACA job): incremental Graph delta walk of a SharePoint library → enqueues one work item per changed/new file.
- **Classifier** (queue-triggered ACA job): per work item → download → extract → classify (self-consistency) → UPSERT the result to PostgreSQL.
> Decision: [ADR-0012](adr/0012-cloud-two-job-pipeline.md) (two-job pipeline on Azure Container Apps).

## Where we persist

- **Local dev:** a **CSV file** on the local filesystem.
  > Decision: [ADR-0004](adr/0004-csv-file-output.md) (file/CSV vs DB).
- **Production:** **PostgreSQL** (Azure Database for PostgreSQL Flexible Server), via SQLAlchemy — durable per-file state (`sync_state`, `documents`, `processing_log`) so runs are incremental, resumable, and re-triggerable. The output destination is a strategy behind one `writer` seam, selected by entry point.
  > Decision: [ADR-0013](adr/0013-postgresql-state-store.md) (PostgreSQL vs CSV / Azure SQL).

## Method

**LLM-based classification using `claude-haiku-4-5`.** For each document: extract its text, then classify it against the category definitions + few-shot examples drawn from the Markdown file. The model assigns exactly one category from the defined set **or the reserved `unknown` category** when nothing fits, plus a **confidence** value. Low-confidence / `unknown` rows surface uncertainty for human review rather than being silently forced into a real category.
> Decisions: [ADR-0001](adr/0001-llm-based-classification.md) (LLM vs rules vs classical ML); [ADR-0002](adr/0002-model-haiku-4-5.md) (model = Haiku 4.5).

**Confidence via self-consistency.** Logprobs are unavailable on the Anthropic Messages API (Haiku 4.5), so confidence is derived by **classifying each document N times and using the agreement rate as the score** (e.g. 5/5 identical → 1.0, 3/5 → 0.6). The winning category is the modal (most-frequent) label across the N runs; ties or a modal count at/below a threshold resolve to `unknown`.
> Decision: [ADR-0005](adr/0005-confidence-self-consistency.md) — N-run self-consistency (default N configurable, e.g. 5) + reserved `unknown` category. Logprob-based scoring was rejected because the Anthropic API does not expose logprobs. Open tunables (N, the tie/threshold rule) are pinned during implementation planning.

**Prompt & output format.** Each call returns exactly one category via **structured output** (`output_config.format`, JSON schema whose `category` field is an `enum` of the defined categories + `unknown`, built once at run start), **label-only** (no per-call reasoning). The static category block (definitions + few-shot examples) is placed first as a **prompt-cache prefix**; the document text goes last. Variation across the N self-consistency runs comes from `temperature`.
> Decision: [ADR-0008](adr/0008-prompt-structured-output.md). Tunables (`temperature`, over-context handling) pinned during implementation planning.

## Constraints / Rules

- **Single-label:** each file is assigned exactly one category (categories are mutually exclusive). `confidence` is metadata, not a second label.
- **Reserved `unknown` category:** there is always an `unknown` bucket for documents that fit no defined category; the model is never forced to guess a real category. Uncertainty is expressed via both `unknown` and `confidence`.
- **Config-driven label set:** the *real* categories come from the Markdown file, not code (`unknown` is the one built-in). A real category not defined in the Markdown file is never emitted. The category file keeps the **existing A1 format** (`## <name>` + description + `-` bullet few-shot examples) and supplies **label definitions + examples only** — it carries no output/confidence/reasoning instructions. The **code owns the output contract** (label-only structured output + self-consistency confidence), and the single reserved catch-all is `unknown` (any `other`-style bucket in a source taxonomy maps to `unknown`).
- Supported input types are exactly PDF, DOCX, DOC; anything else is handled explicitly (see done criteria).

## Text extraction

Per-format Python libraries (PDF, DOCX, legacy `.doc`).
> Decision: [ADR-0006](adr/0006-text-extraction-per-format-libs.md). **Sub-item still open:** the concrete legacy-`.doc` handler is unresolved (see the ADR) — pinned during implementation planning.

## SharePoint access

Microsoft Graph, app-only (client-credentials) auth.
> Decision: [ADR-0007](adr/0007-sharepoint-app-only-auth.md). **Specifics still open:** Azure AD app registration + exact Graph scopes — pinned during implementation planning; may warrant its own build issue.

**Ingestion (walker).** Incremental **Graph delta** queries under a time budget, resumable via a two-token (`delta_token` / `resume_token`) `sync_state`; `file.hashes` content-hash change detection (ADR-0017); enqueue is idempotent (in-flight / unchanged files are skipped). Files outside `/Matters/` are tracked `skipped`, not classified; each document's raw folder path is stored (ADR-0018) rather than a derived matter.
> Decision: [ADR-0014](adr/0014-sharepoint-delta-walker.md).

**Download (classifier).** Files stay in SharePoint; the classifier fetches bytes on demand via an authenticated `GET .../items/{id}/content` into memory (no local copy), and extraction reads the byte stream directly.
> Decision: [ADR-0015](adr/0015-graph-authenticated-download.md).

## Scale

- **Local dev (v1):** target **< 100 files per run**, sequential — no concurrency or resumability.
- **Production (v2):** thousands of files, **incremental after the first pass** (deltas return only changes). Horizontal scale is **queue-driven** — Azure/KEDA spawns classifier replicas from queue depth; the codebase runs one document per invocation. The first full enumeration may span several scheduled walker slots (resumable).
> Decision: [ADR-0012](adr/0012-cloud-two-job-pipeline.md), [ADR-0014](adr/0014-sharepoint-delta-walker.md).

## Deferred implementation specifics

All architectural decisions are made (see the ADRs). These details are deliberately left to per-issue implementation planning:

- Concrete legacy-`.doc` handler ([ADR-0006](adr/0006-text-extraction-per-format-libs.md)).
- Graph app registration + exact scopes ([ADR-0007](adr/0007-sharepoint-app-only-auth.md)).
- `temperature` and `N` tuning, and over-context (large-document) handling ([ADR-0008](adr/0008-prompt-structured-output.md)).
- PostgreSQL schema/migrations, SQLAlchemy models, and the `DatabaseWriter` ([ADR-0013](adr/0013-postgresql-state-store.md)).
- Walker delta-loop, queue-message contract, and `graph_client` ([ADR-0014](adr/0014-sharepoint-delta-walker.md), [ADR-0015](adr/0015-graph-authenticated-download.md)).
- Azure infrastructure (ACA env + two jobs, Queue Storage, registry, Key Vault, managed identity, Log Analytics) as IaC, plus the Dockerfile ([ADR-0012](adr/0012-cloud-two-job-pipeline.md)).
- Authoring the production category Markdown file in the existing A1 format (label definitions + few-shot examples; any `other`-style bucket → `unknown`).

## Acceptance criteria

Product-level, observable behaviors that define done. These are *what to verify*; the executable TDD unit tests that assert them are authored per issue and live in `tests/` — not here. The spec carries acceptance criteria, the tests carry the concrete assertions; the two are kept at separate altitudes so the spec doesn't churn every time an issue-level test is added.

- Given a local PDF (and a DOCX, and a DOC) plus a category file, the tool produces a CSV row assigning the correct single category and a confidence value to each.
- Every file in a target source (local dir or SharePoint location) appears **exactly once** in the output (CSV row / `documents` row).
- An unsupported file type is handled explicitly, not silently mis-processed. At the **source** (enumeration) it is **skipped with a `WARNING`** so a run over a mixed directory still proceeds ([ADR-0010](adr/0010-uniform-document-source.md)); at **extraction** time a file pointed at directly is rejected with `UnsupportedFormatError` ([ADR-0006](adr/0006-text-extraction-per-format-libs.md), [ADR-0009](adr/0009-defer-legacy-doc-extraction.md)).
- The allowed categories are read from the Markdown file; a category not defined there is never emitted.
- SharePoint and local-filesystem sources produce the same result shape for equivalent files (CSV row / `documents` row).
- **(v2)** Every changed/new file in the library appears exactly once as a work item and one `documents` row; unchanged and in-flight files are not re-enqueued.
- **(v2)** An interrupted walk resumes from its saved page; a completed walk advances the delta token; re-classification is a `status='pending'` reset that never overwrites a manual `classification_override`.
- (Criteria refined as issues are planned; each issue decomposes the relevant ones into concrete tests.)
