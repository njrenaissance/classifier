# ADR-0006 — Per-format Python text extraction

Status: accepted

## Context

LLM classification ([[0001-llm-based-classification]]) needs the document's text. Inputs are PDF, DOCX, and legacy binary DOC, running on Windows. PDF and DOCX have well-supported pure-Python extractors; legacy `.doc` (the old OLE binary format) is the hard case with no strong pure-Python story.

## Decision

Use **per-format Python libraries**: a PDF extractor (e.g. `pypdf`/`pdfplumber`), `python-docx` for DOCX, and a dedicated handler for legacy `.doc`.

## Alternatives

- **Apache Tika** (`tika-python`) — one library handling all three formats uniformly, robust on `.doc`; requires a **Java runtime** on the machine.
- **LibreOffice headless** — convert everything to text via `soffice`; handles `.doc` well but requires LibreOffice installed and incurs process-spawn overhead.

## Tradeoffs

- **Gain:** lightweight and pure-Python for the common PDF/DOCX cases; no JVM or external office suite required for the majority of inputs.
- **Give up:** **legacy `.doc` is the weak link** — a pure-Python `.doc` extractor may be unreliable, and the fallback may still require LibreOffice/Word under the hood, partially eroding the "no external dependency" benefit for that one format.

## Consequences

- **OPEN → Resolved:** the concrete `.doc` handling approach is unresolved and must be pinned during implementation planning — if it forces a LibreOffice/Word dependency, reconsider Apache Tika via a superseding ADR. **Pinned by [[0009-defer-legacy-doc-extraction]]: `.doc` is deferred (rejected as unsupported for now); the concrete handler is chosen in a later, superseding ADR.**
- Extraction failures per format must be handled explicitly (skip-with-warning vs error — an implementation-planning detail), consistent with the spec's done criteria.
