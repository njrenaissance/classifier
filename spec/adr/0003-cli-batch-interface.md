# ADR-0003 — CLI / batch interface

Status: accepted

## Context

`classifier` needs an entry point. It takes a source of documents (a local path or a SharePoint location) plus a category-definition Markdown file, and produces a CSV. Expected volume is <100 files per run, run on demand rather than as a long-lived service.

## Decision

Ship a **CLI / batch job**: invoked with a source + a category file, it classifies every document and writes a CSV, then exits.

## Alternatives

- **Library** (importable `classify()` API) — maximum embeddability, but needs a caller; not usable stand-alone by an operator.
- **Service** (HTTP endpoint) — good for continuous/on-demand remote use, but adds hosting, auth, and lifecycle concerns unjustified for on-demand batch runs.

## Tradeoffs

- **Gain:** simplest thing that runs end-to-end; scriptable and schedulable; no server to operate.
- **Give up:** not directly embeddable in another process without shelling out; no always-on API surface.

## Consequences

- Structure the core as importable functions with a thin CLI wrapper, so a library or service can be added later without a rewrite.
- Output destination is a CSV file — see [[0004-csv-file-output]].
