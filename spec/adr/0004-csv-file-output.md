# ADR-0004 — CSV file output

Status: accepted

## Context

The classifier must persist its results: for each document, the assigned category and a confidence value. Consumers are humans reviewing/triaging results and possibly a spreadsheet. Volume is <100 rows per run.

## Decision

Write results to a **CSV file on the local filesystem**, columns `filename`, `category`, `confidence`. No database.

## Alternatives

- **Database** (SQLite / Postgres) — queryable, accumulates history across runs, but adds a schema, a dependency, and operational overhead not justified for <100 rows consumed as a flat list.
- **stdout only** — simplest, but not durable and awkward for spreadsheet hand-off.

## Tradeoffs

- **Gain:** trivially portable, opens in any spreadsheet, zero infrastructure, easy to diff/review.
- **Give up:** no cross-run history, no querying, no concurrent-writer story.

## Consequences

- Each run produces a standalone CSV; historical tracking (if ever needed) is a future, separately-recorded decision.
- Local FS and SharePoint sources must produce the **same** CSV shape for equivalent files.
