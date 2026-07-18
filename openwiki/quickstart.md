---
type: Guide
title: OpenWiki Quickstart
description: Start here to understand the classifier architecture, workflows, and where to go next
tags: [overview, guide, classifier, document-classification]
---

# Classifier — OpenWiki Quickstart

Welcome to the classifier wiki. This is a **document classifier powered by Claude Haiku 4.5**, designed to automatically categorize documents (PDF, DOCX) into user-defined categories with confidence scores.

## What This Project Does

The classifier reads documents from your local filesystem or SharePoint, extracts their text, and assigns each one to exactly one category from a user-provided category definition (Markdown file). It outputs a CSV with columns: `filename`, `category`, `confidence`.

**Key features:**
- **LLM-based**: Uses Claude Haiku 4.5 for semantic understanding
- **Confidence via self-consistency**: Runs classification N times and reports agreement rate as confidence
- **Config-driven labels**: Categories come from a Markdown file, never hardcoded
- **Structured output**: The model cannot invent or paraphrase labels — they must match the defined set
- **Prompt caching**: The category definitions are cached to reduce latency and API cost

See [spec/spec.md](../spec/spec.md) for the full product specification.

## Architecture at a Glance

The system has **four main layers**:

1. **Sources** (A3) — Enumerate documents to classify (local filesystem or SharePoint)
2. **Extraction** (A2) — Extract plain text from PDFs and DOCX files
3. **Classifier Core** (B1) — Single LLM call using structured output
4. **Self-Consistency + CSV** (B2/A4) — Vote over N runs, then write CSV results

See [architecture/overview.md](architecture/overview.md) for the full diagram and layer descriptions.

## Key Concepts

- **CategorySet** — The parsed categories from the Markdown file; includes the reserved `unknown` bucket
- **Classifier** — Wraps one API call; builds a static prompt-cache prefix for efficiency
- **SelfConsistencyClassifier** — Votes over N single-call classifications to produce a (category, confidence) verdict
- **DocumentSource** — Protocol for enumerating documents; implemented by LocalFileSystemSource and (future) SharePoint source
- **TextExtractor** — Strategy for extracting text; PDF and DOCX extractors are registered; legacy `.doc` is deferred

## Where to Go Next

- **[Architecture](architecture/overview.md)** — Layer breakdown, component relationships, design patterns
- **[Workflows](workflows/classification-pipeline.md)** — How a document flows through the system from source to CSV
- **[Domain Concepts](domain/)** — Categories, text extraction, self-consistency voting, error handling
- **[Operations](operations/)** — Configuration, CLI entry point, error types
- **[Tests](testing.md)** — Testing strategy and key test patterns
- **[ADRs](../spec/adr/README.md)** — Architectural decisions behind the design

## Project Layout

```
├── spec/
│   ├── spec.md          # Product spec and acceptance criteria
│   └── adr/             # Architectural decision records (ADR-0001 through ADR-0011)
├── src/
│   ├── main.py          # CLI entry point (TBD — under construction)
│   ├── config.py        # Pydantic-settings configuration management
│   ├── categories.py    # Markdown category parser (A1)
│   ├── extraction.py    # Text extraction strategies (A2)
│   ├── sources.py       # Document source protocol & LocalFileSystemSource (A3)
│   ├── classifier.py    # Core LLM classifier with structured output (B1)
│   ├── self_consistency.py  # N-run voting & confidence (B2)
│   ├── writer.py        # CSV results writer (A4)
│   └── errors.py        # Exception hierarchy
├── tests/               # Unit and integration tests
├── pyproject.toml       # Python project config (uv, pytest, ruff, mypy)
├── CLAUDE.md            # Agent instructions (OpenWiki standard)
└── openwiki/            # This wiki
```

## Setup & Running

See [README.md](../README.md) for setup and test/lint/format commands.

The CLI is under construction. Once complete, usage will be:
```bash
uv run python src/main.py --source <path> --categories <category-file.md> --output results.csv
```

## Configuration

The application reads settings from the environment or a `.env` file via [Pydantic Settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/):

- `ANTHROPIC_API_KEY` — Anthropic API key (required)
- `CLASSIFIER_N` — Number of self-consistency runs (default: 5)
- `CLASSIFIER_TEMPERATURE` — Temperature for LLM sampling (default: 0.4, range: [0.0, 1.0])
- `CLASSIFIER_CONFIDENCE_THRESHOLD` — Confidence threshold; labels at/below this resolve to `unknown` (default: 0.6, range: [0.0, 1.0])

See [operations/configuration.md](operations/configuration.md) for details.

## Important Design Rules

- **Single label per document** — Each file gets exactly one category; categories are mutually exclusive
- **Reserved `unknown` category** — Always available when nothing fits; the model is never forced to guess
- **Config-driven labels** — Categories come from the Markdown file, never hardcoded
- **Deterministic output** — The static category block (prompt-cache prefix) and schema are built once and reused, so multiple runs over the same data produce stable, cacheable requests
- **No silent failures** — Unsupported file types and extraction errors are surfaced, not swallowed

See [spec/spec.md](../spec/spec.md) for acceptance criteria and the ADRs (linked from [architecture/overview.md](architecture/overview.md)) for design rationale.

## Backlog

- **CLI implementation** (main.py orchestration) — Wires together sources, extraction, classifier, and writer
- **SharePoint source** (D1) — Implements DocumentSource protocol for Microsoft Graph-backed file enumeration (app-only auth)
- **Legacy `.doc` extraction** — Binary DOC format handler (currently deferred; see ADR-0006 and ADR-0009)
- **Performance tuning** — N, temperature, and context-overflow handling (tunables pinned during implementation planning; see ADR-0005, ADR-0008)
