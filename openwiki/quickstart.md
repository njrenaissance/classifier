---
type: Guide
title: Classifier — OpenWiki Quickstart
description: Start here to understand the classifier's two deployment modes (local CLI and cloud pipeline), key concepts, and navigation to detailed documentation
tags: [overview, guide, classifier, document-classification]
verified:
  - by: openwiki/0.4.3
    at: 2026-08-28T19:49:26.700Z
sources:
  - id: openwiki-source-5f5b95b3d6a215fa02ceb945
    resource: repo://.env.example
  - id: openwiki-source-9493b522b46e6e73db26bc3a
    resource: repo://categories.md
  - id: openwiki-source-862443b88cee5adeb9e4ba55
    resource: repo://infra/README.md
  - id: openwiki-source-23775c3de52f3ab95a13cb8b
    resource: repo://README.md
  - id: openwiki-source-8972c7bf69bcfa3714a54942
    resource: repo://spec/spec.md
  - id: openwiki-source-807e457ee195ce615a28069e
    resource: repo://src/categories.py
  - id: openwiki-source-d502c275990c6476221bf080
    resource: repo://src/config.py
  - id: openwiki-source-3ecb73aeca5d8558f557e1ab
    resource: repo://src/extraction.py
  - id: openwiki-source-11b9d806fcc6dd6e7747ed87
    resource: repo://src/main.py
  - id: openwiki-source-d6651d3bc51203d33893f15c
    resource: repo://src/processor.py
  - id: openwiki-source-000177df6efd47c978dca405
    resource: repo://src/self_consistency.py
  - id: openwiki-source-0b08ad2ade4feed2ba3e8fa7
    resource: repo://src/walker.py
generated: { by: "openwiki/0.4.3", at: "2026-08-28T19:49:26.700Z" }
---

# Classifier — OpenWiki Quickstart

Welcome to the classifier wiki. This is a **document classifier powered by Claude or Claude via Microsoft Foundry**, designed to automatically categorize documents (PDF, DOCX, plain text formats) into user-defined categories with confidence scores.

## Two Deployment Paths

The system supports **two equally important deployment modes**, each with its own orchestration and use case:

| Path | Entry Point | Scope | Use Case |
|------|-------------|-------|----------|
| **Local** | `uv run python src/main.py` (CLI) | Single machine, batch | Development, rapid testing, on-machine classification |
| **Local Testing Stack** | `docker compose -f infra/docker-compose.yml up` | Real queue + database, mounted filesystem | Integration testing, live-fire validation against your documents (ADR-0020) |
| **Cloud** | `python -m walker` + `python -m processor` | Distributed, SharePoint-sourced | Production: incremental, resumable SharePoint → PostgreSQL pipeline |

**Key concept:** The "local" category includes both the simple CLI and the full two-job testing stack; both exercise the real code paths with actual documents and state persistence. The cloud path is the same architecture running at scale against SharePoint and Azure infrastructure.

## What This Project Does

The classifier reads documents from your local filesystem, a mounted directory, or SharePoint, extracts their text, and assigns each one to exactly one category from a user-provided category definition (Markdown file). Each path outputs results according to its architecture: the local CLI writes a CSV with columns `filename`, `category`, `confidence`; the cloud pipeline persists results to PostgreSQL and coordinates work via Azure Queue.

**Key features:**
- **LLM-based**: Uses Claude Haiku 4.5 via Anthropic or Microsoft Foundry
- **Confidence via self-consistency**: Runs classification N times and reports agreement rate as confidence
- **Config-driven labels**: Categories come from a Markdown file, never hardcoded
- **Structured output**: The model cannot invent or paraphrase labels — they must match the defined set
- **Prompt caching**: The category definitions are cached to reduce latency and API cost
- **Cloud ready**: Two-job pipeline (walker + processor) decoupled by Azure Queue, stateless consumers scale with KEDA
- **Resumable enumeration**: Walker delta-walks SharePoint with time budgets, persists position across scheduled runs
- **Idempotent**: Tracks file content hash and processing status to prevent duplicate work
- **Filesystem source (ADR-0020)**: Both walker and processor support local directory enumeration for testing without SharePoint/Graph

See [spec/spec.md](../spec/spec.md) for the full product specification.

## Quick Start by Path

### Local CLI (Fastest)

Point the CLI at a local file or directory plus a categories Markdown file; it enumerates documents, classifies them, and writes a CSV:
```bash
export ANTHROPIC_API_KEY=sk-...   # required
uv run python src/main.py ./docs -c categories.md -o results.csv
```

- **Docs:** [Classification Pipeline Workflow](workflows/classification-pipeline.md)
- **Configuration:** [Configuration Management](operations/configuration.md) (inference provider selection, tuning knobs)
- **Setup:** [README.md](../README.md)

### Local Testing Stack (Realistic)

Run the full two-job pipeline locally against a mounted directory with real PostgreSQL and Azurite queue, no GraphAPI credentials needed:
```bash
docker build -t classifier:ci .
docker compose -f infra/docker-compose.yml up
```

- **Docs:** [Filesystem Pipeline Workflow](workflows/filesystem-pipeline.md), [Local Testing and Live-Fire Stack](operations/local-testing.md)
- **Live-fire:** Real LLM calls → real cost per run; faked voter → free automated tests
- **Status:** ADR-0020; filesystem source is production-ready for both walker and processor

### Cloud Pipeline (Production)

Distributed two-job system running on Azure Container Apps, enumerating SharePoint and persisting to PostgreSQL:
```bash
docker run --rm --env-file .env classifier python -m walker      # scheduled producer
docker run --rm --env-file .env classifier python -m processor   # queue-triggered consumer
```

- **Docs:** [Cloud Pipeline Workflow](workflows/cloud-pipeline.md)
- **Configuration:** [Configuration Management](operations/configuration.md) (Graph, queue, database, inference provider)
- **Deployment:** [Deployment Guide](operations/deployment.md)
- **State:** [State Store and PostgreSQL Schema](operations/state-store.md)

## Architecture at a Glance

All paths share a **common classification core** (category parsing, text extraction, LLM classifier, self-consistency voting) but differ in how they enumerate documents, persist state, and handle orchestration.

**Common layers:**
1. **Categories** (A1) — Parse Markdown category definitions
2. **Extraction** (A2) — Extract plain text from PDFs, DOCX, and plain-text formats (.txt, .json, .yaml, .md, .csv, .xml)
3. **Classifier Core** (B1) — Single LLM call using structured output
4. **Self-Consistency** (B2) — Vote over N runs to determine confidence

**Path-specific**:
- **Local CLI**: DocumentSource (local filesystem) → CSV output
- **Local Testing Stack**: FilesystemWalker → Queue (Azurite) → Processor → PostgreSQL output
- **Cloud Pipeline**: Walker → GraphClient (SharePoint) → Queue (Azure Storage) → Processor → PostgreSQL output

See [Architecture Overview](architecture/overview.md) for full diagrams and layer descriptions.

## Key Concepts

### Core Classification
- **CategorySet** — Parsed categories from Markdown; includes reserved `unknown` bucket
- **Classifier** — Wraps one API call; builds a static prompt-cache prefix
- **SelfConsistencyClassifier** — Votes over N calls to produce (category, confidence) verdict
- **TextExtractor** — Strategy for extracting text; PDF, DOCX, and plain-text extractors are registered

### State and Persistence
- **Local CLI:** CSV file output; no state persistence
- **Cloud Pipeline (v2):**
  - **SyncState** — Persisted walker position: delta token, resume token, completion status
  - **Document** — State row tracking file identity, content hash, processing status, and classification result
  - **Message** — Work item passed from walker to processor via Azure Queue
  - **ProcessingLog** — Audit trail of per-message classification attempts

### Document Sources (ADR-0020, ADR-0010)
- **LocalFileSystemSource** — Filesystem enumeration for CLI; skips unsupported file types
- **FilesystemWalker** — Full re-enumeration of mounted directory; no delta token, but hash-based idempotency
- **GraphClient** — SharePoint enumeration via Microsoft Graph delta queries; resumable across time budgets

See [Document Sources and Pluggable Seams](architecture/sources-and-seams.md) for details.

## Navigation by Audience

### I want to understand the system
- **[Architecture Overview](architecture/overview.md)** — Both deployment paths, component relationships, design patterns
- **[System Architecture Sources and Seams](architecture/sources-and-seams.md)** — How document enumeration is abstracted (local filesystem vs. SharePoint)

### I want to develop locally
- **[Classification Pipeline](workflows/classification-pipeline.md)** — Local CLI: document enumeration through CSV output
- **[Local Testing and Live-Fire Stack](operations/local-testing.md)** — Run the full two-job pipeline with your documents (filesystem source)
- **[Configuration](operations/configuration.md)** — Set up inference provider (Anthropic or Foundry)
- **[README.md](../README.md)** — Setup and run commands

### I want to run integration tests
- **[Local Testing and Live-Fire Stack](operations/local-testing.md)** — Free `pytest -m integration` (faked voter) vs. docker-compose (real LLM)
- **[Filesystem Pipeline Workflow](workflows/filesystem-pipeline.md)** — How the two-job pipeline works against mounted directories (ADR-0020)

### I want to deploy to production
- **[Cloud Pipeline Workflow](workflows/cloud-pipeline.md)** — Full workflow: walker → queue → processor → database
- **[Configuration](operations/configuration.md)** — Inference provider, Graph auth, queue, database, deployment tuning
- **[Deployment Guide](operations/deployment.md)** — Container build, CI gates, OIDC, container registry
- **[State Store](operations/state-store.md)** — PostgreSQL schema, document lifecycle
- **[Cloud Boundaries](operations/cloud-boundaries.md)** — Message queue, Graph client, auth modes
- **[Error Handling](operations/error-handling.md)** — Exception types, retry logic, poison messages

### I want to understand the design decisions
- **[spec/spec.md](../spec/spec.md)** — Acceptance criteria and product scope
- **[spec/adr/](../spec/adr/)** — Architectural Decision Records (ADR-0001 through ADR-0020+)
  - **ADR-0020** — Filesystem source (local directory enumeration instead of SharePoint)
  - **ADR-0012** — Two-job cloud pipeline architecture
  - **ADR-0013** — PostgreSQL state store
  - **ADR-0005** — Self-consistency voting for confidence

### I want to understand a specific domain
- **[Categories and Markdown Parsing](domain/category-parsing.md)** — How categories are defined and parsed
- **[Text Extraction and Format Support](domain/text-extraction.md)** — PDF, DOCX, plain-text extractors, per-format libraries
- **[Self-Consistency Voting](domain/self-consistency.md)** — N-run voting, confidence calculation, temperature tuning
- **[Document Sources](domain/document-sources.md)** — Source abstraction, LocalFileSystemSource, GraphClient enumeration

### I want to test or extend the code
- **[Testing Guide](testing.md)** — Testing strategy, key test patterns, unit and integration tests
- **[Configuration](operations/configuration.md)** — Tuning parameters (N, temperature, confidence threshold)

## Project Layout

```
├── spec/
│   ├── spec.md          # Product spec and acceptance criteria
│   └── adr/             # Architectural decision records
├── src/
│   ├── main.py          # Local CLI entry point
│   ├── walker.py        # Cloud producer: SharePoint enumeration + queue
│   ├── processor.py     # Cloud consumer: dequeue → download → classify → persist
│   ├── config.py        # Pydantic-settings configuration management
│   ├── categories.py    # Markdown category parser (A1)
│   ├── extraction.py    # Text extraction strategies (A2)
│   ├── sources.py       # LocalFileSystemSource (CLI enumeration)
│   ├── filesystem_walker.py # FilesystemWalker (cloud pipeline, filesystem source)
│   ├── content_source.py    # ContentSource protocol (graph vs filesystem download)
│   ├── classifier.py    # Core LLM classifier with structured output (B1)
│   ├── self_consistency.py  # N-run voting & confidence (B2)
│   ├── writer.py        # CSV output (local) + DatabaseWriter (cloud)
│   ├── db.py            # PostgreSQL models & session management (cloud)
│   ├── enqueuer.py      # Shared idempotent enqueue logic (walker → queue)
│   ├── message_queue.py # Azure Queue abstraction (cloud)
│   ├── graph_client.py  # Microsoft Graph API client (cloud)
│   ├── models.py        # Pydantic models (Message, DocumentClassification)
│   └── errors.py        # Exception hierarchy
├── infra/
│   ├── docker-compose.yml   # Live-fire stack (filesystem source + Azurite + Postgres)
│   └── README.md            # How to run the local stack
├── alembic/             # Database schema migrations
├── tests/               # Unit and integration tests
├── Dockerfile           # Container image (both entry points)
├── pyproject.toml       # Python project config (uv, pytest, ruff, mypy)
├── CLAUDE.md            # Agent instructions (OpenWiki standard)
└── openwiki/            # This wiki
```

## Important Design Rules

- **Single label per document** — Each file gets exactly one category; categories are mutually exclusive
- **Reserved `unknown` category** — Always available when nothing fits; the model is never forced to guess
- **Config-driven labels** — Categories come from the Markdown file, never hardcoded
- **Deterministic output** — The static category block (prompt-cache prefix) and schema are built once and reused, so multiple runs over the same data produce stable, cacheable requests
- **No silent failures** — Unsupported file types and extraction errors are surfaced, not swallowed
- **Idempotent enqueue** — Files already in progress or whose content hash is unchanged are never re-enqueued
- **No hardcoded scoping** — The SharePoint library root is configurable (default `/Matters`), enforced at the Graph delta level (ADR-0019)

See [spec/spec.md](../spec/spec.md) for acceptance criteria and the ADRs (linked from [architecture/overview.md](architecture/overview.md)) for design rationale.

## Setup & Running

### Local CLI
```bash
uv run python src/main.py ./docs -c categories.md -o results.csv
```
See [README.md](../README.md) for setup and test/lint/format commands.

### Local Testing Stack (filesystem source)
```bash
docker build -t classifier:ci .
docker compose -f infra/docker-compose.yml up
```
See [infra/README.md](../infra/README.md) and [Local Testing and Live-Fire Stack](operations/local-testing.md).

### Cloud Container
```bash
docker build -t classifier .

# Producer: enumerate SharePoint and enqueue work
docker run --rm --env-file .env classifier python -m walker

# Consumer: process one queued document
docker run --rm --env-file .env classifier python -m processor
```
See [Deployment Guide](operations/deployment.md) for the full container setup and CI workflow.

## Configuration

All settings are loaded from environment variables or a `.env` file via [Pydantic Settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/). 

**Local CLI requires:**
- `ANTHROPIC_API_KEY` or `CLASSIFIER_FOUNDRY_*` settings (inference provider)
- Classification tuning: `CLASSIFIER_N`, `CLASSIFIER_TEMPERATURE`, `CLASSIFIER_CONFIDENCE_THRESHOLD` (optional, defaults provided)

**Cloud pipeline requires additional settings:**
- Database: `DATABASE_URL`, `DB_USERNAME`, `DB_PASSWORD`
- Microsoft Graph: `CLASSIFIER__GRAPH_TENANT_ID`, `CLASSIFIER__GRAPH_CLIENT_ID`, `CLASSIFIER__GRAPH_CLIENT_SECRET`, `CLASSIFIER__GRAPH_DRIVE_ID`
- Queue: `CLASSIFIER__QUEUE_CONNECTION_STRING`
- Processor: `CLASSIFIER__PROCESSOR_CATEGORY_FILE`

**Filesystem source (ADR-0020) requires:**
- `CLASSIFIER_SOURCE=filesystem` (instead of `sharepoint`)
- `CLASSIFIER__FILESYSTEM_ROOT=/path/to/documents` (mounted directory for walker + processor)
- No Graph credentials needed

See [Configuration Management](operations/configuration.md) for the complete reference, including all environment variables, defaults, and selection guidance for Anthropic vs. Microsoft Foundry inference.

## Backlog

- **Legacy `.doc` extraction** — Binary DOC format handler (currently deferred; see ADR-0006 and ADR-0009)
- **Category file authoring and deployment** (#42) — Pipeline for defining and managing category definitions in production
- **Performance tuning** — Context-overflow handling and context budget planning (see ADR-0005, ADR-0008)
