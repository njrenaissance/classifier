# ADR-0009 — Defer legacy `.doc` extraction

Status: accepted

## Context

[[0006-text-extraction-per-format-libs]] chose per-format Python libraries for text extraction and deliberately left one item **OPEN**: the concrete legacy `.doc` (OLE binary) handler, "to be pinned during Phase 1." A2 ([issue #6](https://github.com/njrenaissance/classifier/issues/6)) implements the two formats with a strong pure-Python story — PDF via `pypdf`, DOCX via `python-docx` — but `.doc` remains the weak link ADR-0006 flagged: there is no maintained, reliable pure-Python `.doc` extractor, and the robust options each drag in a heavyweight external runtime that the per-format-libs decision was chosen to avoid for the common cases.

This ADR pins that open sub-decision. It does **not** revisit ADR-0006's core decision (per-format libraries), which stands.

## Decision

**Defer legacy `.doc` support.** It is not implemented now. `.doc` is treated as an unregistered suffix and rejected explicitly with `UnsupportedFormatError` (surfaced, never silently skipped), exactly like any other unsupported type. A2's shipped scope is **PDF + DOCX only**.

The concrete `.doc` handler remains unchosen — deferred, not decided — and is tracked as a separate follow-up issue to be planned (Phase 1) when there is real `.doc` volume to justify committing to a dependency.

## Alternatives

Considered for handling `.doc` *now*, and why each is deferred rather than adopted:

- **Apache Tika** (`tika-python`) — robust on `.doc`, but requires a **JVM** on every (Windows) host. Rejected for now to keep the common path pure-Python, consistent with ADR-0006.
- **LibreOffice headless** (`soffice`) — handles `.doc` well, but requires the full office suite installed and incurs process-spawn overhead per file. Same reason.
- **Pure-Python `.doc` readers** (`olefile`/antiword-style parsing) — no maintained option is reliable enough on real-world `.doc` to meet the bar.

Deferring avoids committing to a heavyweight, always-on dependency before a concrete `.doc` need exists to justify it.

## Tradeoffs

- **Gain:** A2 ships now — pure-Python, no JVM or office-suite dependency. The choice of a `.doc` engine is made later, against real data, rather than speculatively.
- **Give up:** `.doc` inputs are rejected until a follow-up implements them; users with legacy `.doc` files must convert to PDF/DOCX in the meantime.

## Consequences

- `extract_text()` raises `UnsupportedFormatError` for `.doc` (as for any unregistered suffix) — explicit and surfaced, consistent with the spec's "failures surfaced, not swallowed" done criteria.
- Issue #6's original acceptance criterion "text extracted for a sample DOC" is **descoped from A2** and tracked as a separate follow-up issue; the PDF/DOCX criteria are unchanged.
- When `.doc` is picked up, **this ADR is superseded** by one that chooses the concrete handler (Tika / LibreOffice / other). If that choice forces an external runtime, ADR-0006's Apache Tika alternative is reconsidered at that point, as ADR-0006 anticipated.
- ADR-0006's core decision (per-format libraries) is unaffected; this ADR only resolves its open `.doc` sub-decision.
