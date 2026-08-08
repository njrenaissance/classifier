---
type: Guide
title: OpenWiki Quickstart
description: Start here to understand the classifier architecture, workflows, and where to go next
tags: [overview, guide, classifier, document-classification]
---

# Classifier — OpenWiki Quickstart

Welcome to the classifier wiki. This is a **document classifier powered by Claude or Claude via Microsoft Foundry**, designed to automatically categorize documents (PDF, DOCX) into user-defined categories with confidence scores.

The system supports **two deployment modes**:
1. **Local CLI** — single-machine batch processing, writes CSV results
2. **Cloud Pipeline** — distributed multi-job system with SharePoint integration, Azure Queue decoupling, and PostgreSQL persistence

## What This Project Does

The classifier reads documents from your local filesystem or SharePoint, extracts their text, and assigns each one to exactly one category from a user-provided category definition (Markdown file). The local CLI outputs a CSV with columns: `filename`, `category`, `confidence`. The cloud pipeline persists results to PostgreSQL and coordinates work via Azure Queue.

**Key features:**
- **LLM-based**: Uses Claude Haiku 4.5 via Anthropic or Microsoft Foundry
- **Confidence via self-consistency**: Runs classification N times and reports agreement rate as confidence
- **Config-driven labels**: Categories come from a Markdown file, never hardcoded
- **Structured output**: The model cannot invent or paraphrase labels — they must match the defined set
- **Prompt caching**: The category definitions are cached to reduce latency and API cost
- **Cloud ready**: Two-job pipeline (walker + processor) decoupled by Azure Queue, stateless consumers scale with KEDA
- **Resumable enumeration**: Walker delta-walks SharePoint with time budgets, persists position across scheduled runs
- **Idempotent**: Tracks file content hash and processing status to prevent duplicate work

See [spec/spec.md](../spec/spec.md) for the full product specification.

## Deployment Paths

### Local CLI (v1)
Point the CLI at a local file or directory plus a categories Markdown file; it enumerates documents, classifies them, and writes a CSV:
```bash
uv run python src/main.py ./docs -c categories.md -o results.csv
```

### Cloud Pipeline (v2)
Docker image with two role-based entry points, decoupled by Azure Queue and backed by PostgreSQL:
- **Walker** (scheduled job) — Delta-walks SharePoint, enqueues work
- **Processor** (queue-triggered job) — Dequeues, downloads, extracts, classifies, persists result

See [README.md](../README.md) for container usage and [workflows/cloud-pipeline.md](workflows/cloud-pipeline.md) for the distributed flow.

## Architecture at a Glance

Both paths share a **common classification core** (category parsing, text extraction, LLM classifier, self-consistency voting) but differ in how they enumerate documents, persist state, and handle orchestration.

**Common layers:**
1. **Categories** (A1) — Parse Markdown category definitions
2. **Extraction** (A2) — Extract plain text from PDFs and DOCX files
3. **Classifier Core** (B1) — Single LLM call using structured output
4. **Self-Consistency** (B2) — Vote over N runs to determine confidence

**Path-specific**:
- **Local path**: DocumentSource (local filesystem) → CSV output
- **Cloud path**: Walker → Queue → Processor → PostgreSQL output

See [architecture/overview.md](architecture/overview.md) for full diagrams and layer descriptions.

## Key Concepts

### Core Classification
- **CategorySet** — Parsed categories from Markdown; includes reserved `unknown` bucket
- **Classifier** — Wraps one API call; builds a static prompt-cache prefix
- **SelfConsistencyClassifier** — Votes over N calls to produce (category, confidence) verdict
- **TextExtractor** — Strategy for extracting text; PDF and DOCX extractors are registered

### Cloud Pipeline (v2)
- **Walker** — Scheduled job enumerating SharePoint via Microsoft Graph delta queries, issuing resumable with time budgets
- **Processor** — Queue-triggered job that classifies one document per invocation, UPSERT result to database
- **SyncState** — Persisted walker position: delta token, resume token, completion status
- **Document** — State row tracking file identity, content hash, processing status, and classification result
- **Message** — Work item passed from walker to processor via Azure Queue

## Where to Go Next

### Understanding the System
- **[Architecture](architecture/overview.md)** — Both deployment paths, component relationships, design patterns
- **[Local Workflow](workflows/classification-pipeline.md)** — Local CLI: document enumeration through CSV output
- **[Cloud Workflow](workflows/cloud-pipeline.md)** — Cloud pipeline: walker → queue → processor → database
- **[Domain Concepts](domain/)** — Categories, text extraction, self-consistency voting, error handling

### Operations & Configuration
- **[Configuration](operations/configuration.md)** — Environment variables, inference provider selection, cloud settings
- **[State Store](operations/state-store.md)** — PostgreSQL schema, document lifecycle, sync state
- **[Cloud Boundaries](operations/cloud-seams.md)** — Message queue, Graph client, auth modes
- **[Deployment](operations/deployment.md)** — Container build, CI gates, OIDC federated credentials
- **[Error Handling](operations/error-handling.md)** — Exception types, retry logic, poison messages

### Development
- **[Tests](testing.md)** — Testing strategy and key test patterns
- **[ADRs](../spec/adr/README.md)** — Architectural decisions behind the design

## Project Layout

```
├── spec/
│   ├── spec.md          # Product spec and acceptance criteria
│   └── adr/             # Architectural decision records (ADR-0001 through ADR-0019)
├── src/
│   ├── main.py          # Local CLI entry point
│   ├── walker.py        # Cloud producer: SharePoint enumeration + queue
│   ├── processor.py     # Cloud consumer: dequeue → download → classify → persist
│   ├── config.py        # Pydantic-settings configuration management
│   ├── categories.py    # Markdown category parser (A1)
│   ├── extraction.py    # Text extraction strategies (A2)
│   ├── sources.py       # Document source protocol & LocalFileSystemSource
│   ├── classifier.py    # Core LLM classifier with structured output (B1)
│   ├── self_consistency.py  # N-run voting & confidence (B2)
│   ├── writer.py        # CSV output (local) + DatabaseWriter (cloud)
│   ├── db.py            # PostgreSQL models & session management (cloud)
│   ├── message_queue.py # Azure Queue abstraction (cloud)
│   ├── graph_client.py  # Microsoft Graph API client (cloud)
│   ├── models.py        # Pydantic models (Message, DocumentClassification)
│   └── errors.py        # Exception hierarchy
├── alembic/             # Database schema migrations
├── tests/               # Unit and integration tests
├── Dockerfile           # Container image (both entry points)
├── pyproject.toml       # Python project config (uv, pytest, ruff, mypy)
├── CLAUDE.md            # Agent instructions (OpenWiki standard)
└── openwiki/            # This wiki
```

## Setup & Running

See [README.md](../README.md) for setup and test/lint/format commands.

### Local CLI
```bash
uv run python src/main.py ./docs -c categories.md -o results.csv
```

### Cloud Container
```bash
docker build -t classifier .

# Producer: enumerate SharePoint and enqueue work
docker run --rm --env-file .env classifier python -m walker

# Consumer: process one queued document
docker run --rm --env-file .env classifier python -m processor
```

See [operations/deployment.md](operations/deployment.md) for the full container setup and CI workflow.

## Configuration

All settings are loaded from environment variables or a `.env` file via [Pydantic Settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/). The local CLI requires only `ANTHROPIC_API_KEY` and the classification knobs (`CLASSIFIER_N`, `CLASSIFIER_TEMPERATURE`, `CLASSIFIER_CONFIDENCE_THRESHOLD`). The cloud pipeline requires additional settings for inference provider selection, database, Microsoft Graph, and Azure Queue.

See [operations/configuration.md](operations/configuration.md) for the complete reference, including all environment variables, defaults, and selection guidance for Anthropic vs Microsoft Foundry inference.

## Important Design Rules

- **Single label per document** — Each file gets exactly one category; categories are mutually exclusive
- **Reserved `unknown` category** — Always available when nothing fits; the model is never forced to guess
- **Config-driven labels** — Categories come from the Markdown file, never hardcoded
- **Deterministic output** — The static category block (prompt-cache prefix) and schema are built once and reused, so multiple runs over the same data produce stable, cacheable requests
- **No silent failures** — Unsupported file types and extraction errors are surfaced, not swallowed

See [spec/spec.md](../spec/spec.md) for acceptance criteria and the ADRs (linked from [architecture/overview.md](architecture/overview.md)) for design rationale.

## Backlog

- **Legacy `.doc` extraction** — Binary DOC format handler (currently deferred; see ADR-0006 and ADR-0009)
- **Category file authoring and deployment** (#42) — Pipeline for defining and managing category definitions in production
- **Performance tuning** — Context-overflow handling and context budget planning (see ADR-0005, ADR-0008)
