# spec — Document Classifier

Status: approved · v2 (cloud pipeline)

## Purpose

Classify document files into exactly one category from a user-defined category set, recording each file's assigned category and a confidence score.

**v1 → v2.** v1 is a local CLI batch job that reads local files and writes a CSV ([ADR-0003](adr/0003-cli-batch-interface.md), [ADR-0004](adr/0004-csv-file-output.md)); it remains the local-development interface. v2 adds the production deployment: an incremental, resumable **SharePoint → PostgreSQL** pipeline running as two Azure Container Apps jobs, decoupled by an Azure queue ([ADR-0012](adr/0012-cloud-two-job-pipeline.md)–[ADR-0015](adr/0015-graph-authenticated-download.md)). The classification method (extract → classify → result) is identical in both.

## Inputs / Outputs

### Input
- **File types:** PDF, DOCX, DOC
- **Sources:**
  - Local filesystem (a file or a directory of files) — local dev
  - SharePoint, via the Microsoft Graph API — production ([ADR-0012](adr/0012-cloud-two-job-pipeline.md))
  - Azure Blob Storage container, via managed identity — alternative production source; the authoritative evidence store ([ADR-0023](adr/0023-blob-source-mode.md))
- **Category definitions:** a Markdown file that defines the allowed categories, each with a description and few-shot examples. This is the single source of the label set — categories are never hardcoded.

### Output
- Fields: `filename` (or file identity), `category`, `confidence`.
- **Production** also stores `raw_response`: the complete, unmodified provider response for the classification call, kept for audit. It is never logged.
- **Local dev:** a **CSV** on the local filesystem (the three fields above; no raw response). **Production:** rows in **PostgreSQL** ([ADR-0013](adr/0013-postgresql-state-store.md)).
> Decision: [ADR-0005](adr/0005-confidence-self-consistency.md) (amended) — `confidence` is the API probability.

## What we produce

**Local dev — a CLI / batch job**: point it at a local path plus a category-definition Markdown file → it writes a CSV.
> Decision: [ADR-0003](adr/0003-cli-batch-interface.md) (CLI/batch vs library vs service).

**Production — a two-job cloud pipeline** (one image, two entry points), decoupled by an Azure Queue:
- **Walker** (scheduled ACA job): incremental Graph delta walk of a SharePoint library → enqueues one work item per changed/new file. Alternatively, with `CLASSIFIER_SOURCE=blob`, a full re-enumeration of an Azure Blob container, hashing each blob from its `sha256` metadata ([ADR-0023](adr/0023-blob-source-mode.md)).
- **Classifier** (queue-triggered ACA job): per work item → download → extract → classify (one Jev call) → UPSERT the result and its raw response to PostgreSQL.
> Decision: [ADR-0012](adr/0012-cloud-two-job-pipeline.md) (two-job pipeline on Azure Container Apps).

## Where we persist

- **Local dev:** a **CSV file** on the local filesystem.
  > Decision: [ADR-0004](adr/0004-csv-file-output.md) (file/CSV vs DB).
- **Production:** **PostgreSQL** (Azure Database for PostgreSQL Flexible Server), via SQLAlchemy — durable per-file state (`sync_state`, `documents`, `processing_log`) so runs are incremental, resumable, and re-triggerable. The output destination is a strategy behind one `writer` seam, selected by entry point.
  > Decision: [ADR-0013](adr/0013-postgresql-state-store.md) (PostgreSQL vs CSV / Azure SQL).

## Method

**Decision-API classification using TypeSafe Jev.** For each document: extract its text, then ask Jev to choose one option from the defined categories (each with its description) plus the reserved `unknown` option. Jev returns exactly one category from the defined set **or `unknown`** when nothing fits. Low-confidence / `unknown` rows surface uncertainty for human review rather than being silently forced into a real category.
> Decisions: [ADR-0022](adr/0022-openai-decisions-api-inference.md) (inference layer = Jev; proposed, gated on early access and the data-handling review). Superseded: [ADR-0001](adr/0001-llm-based-classification.md) (LLM-generated labels), [ADR-0002](adr/0002-model-haiku-4-5.md) (Claude Haiku), [ADR-0016](adr/0016-foundry-inference-provider.md) (Foundry).

**Confidence from the API probability.** Each document is classified with **one call**. The inference API returns a deterministic probability for each option; the `confidence` stored is the probability of the chosen category (e.g. 0.82). Self-consistency (N repeated runs) is not used.
> Decision: [ADR-0005](adr/0005-confidence-self-consistency.md) (amended) — API probability as confidence + reserved `unknown` category.

**Request & result.** Each call asks one Choice question whose criteria are the category names (with descriptions) plus `unknown`. The result is the chosen category, its probability, and the complete provider response, which is stored as `raw_response` but not logged. Documents over the Jev input limit are rejected rather than truncated.
> Decision: [ADR-0022](adr/0022-openai-decisions-api-inference.md).

## Constraints / Rules

- **Single-label:** each file is assigned exactly one category (categories are mutually exclusive). `confidence` is metadata, not a second label.
- **Reserved `unknown` category:** there is always an `unknown` bucket for documents that fit no defined category; the classifier is never forced to pick a real category. Uncertainty is expressed via both `unknown` and `confidence`.
- **Config-driven label set:** the *real* categories come from the Markdown file, not code (`unknown` is the one built-in). A real category not defined in the Markdown file is never emitted. The category file keeps the **existing A1 format** (`## <name>` + description + `-` bullet few-shot examples) and supplies **label definitions + examples only** — it carries no output/confidence/reasoning instructions. The **code owns the output contract** (a single Choice answer over the category set + `unknown`, with the API probability as confidence), and the single reserved catch-all is `unknown` (any `other`-style bucket in a source taxonomy maps to `unknown`).
- Supported input types are exactly PDF, DOCX, DOC; anything else is handled explicitly (see done criteria).

## Text extraction

Per-format Python libraries (PDF, DOCX, legacy `.doc`).
> Decision: [ADR-0006](adr/0006-text-extraction-per-format-libs.md). **Sub-item still open:** the concrete legacy-`.doc` handler is unresolved (see the ADR) — pinned during implementation planning.

## SharePoint access

Microsoft Graph, app-only (client-credentials) auth.
> Decision: [ADR-0007](adr/0007-sharepoint-app-only-auth.md). **Specifics still open:** Azure AD app registration + exact Graph scopes — pinned during implementation planning; may warrant its own build issue.

**Ingestion (walker).** Incremental **Graph delta** queries under a time budget, resumable via a two-token (`delta_token` / `resume_token`) `sync_state`; `file.hashes` content-hash change detection (ADR-0017); enqueue is idempotent (in-flight / unchanged files are skipped). The walk is scoped to a configurable library subtree (`WalkerSettings.root_path`, default `/Matters`), enforced at the Graph delta level (ADR-0019); each document's raw folder path is stored (ADR-0018) rather than a derived matter.
> Decision: [ADR-0014](adr/0014-sharepoint-delta-walker.md).

**Ingestion (blob).** With `CLASSIFIER_SOURCE=blob`, every run re-enumerates the configured container prefix with no resume token. Each blob's content hash is its `sha256` metadata property (downloaded and hashed only when absent), and idempotency follows the same enqueue rule as the other sources. The container must be in an online tier (Hot/Cool/Cold); Archive-tier blobs cannot be downloaded.
> Decision: [ADR-0023](adr/0023-blob-source-mode.md).

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
- Over-context (large-document) handling and the Jev input limit ([ADR-0022](adr/0022-openai-decisions-api-inference.md)).
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
- SharePoint, local-filesystem, and Azure Blob sources produce the same result shape for equivalent files (CSV row / `documents` row).
- **(v2)** Every changed/new file in the library appears exactly once as a work item and one `documents` row; unchanged and in-flight files are not re-enqueued.
- **(v2)** An interrupted walk resumes from its saved page; a completed walk advances the delta token; re-classification is a `status='pending'` reset that never overwrites a manual `classification_override`.
- (Criteria refined as issues are planned; each issue decomposes the relevant ones into concrete tests.)
