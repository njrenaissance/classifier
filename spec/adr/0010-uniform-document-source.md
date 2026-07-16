# ADR-0010 — Uniform `DocumentSource` abstraction

Status: accepted

## Context

`classifier` reads documents from two places: the local filesystem (A3, [issue #7](https://github.com/njrenaissance/classifier/issues/7)) and SharePoint via Microsoft Graph (D1, [issue #12](https://github.com/njrenaissance/classifier/issues/12), [ADR-0007](0007-sharepoint-app-only-auth.md)). Both feed the same downstream pipeline — extraction → self-consistency classification → CSV — and the CLI ([ADR-0003](0003-cli-batch-interface.md)) should not branch on where a document came from. The spec calls for SharePoint to plug in behind "the same source interface as local FS", so that seam needs a name before the second source exists.

A3 also has to pin an open spec item: how an unsupported file type is handled at enumeration time (`spec/spec.md` previously left this "skipped-with-warning or errored — TBD").

## Decision

Define a single `DocumentSource` **Protocol** with one method, `documents() -> Iterable[Path]`, and implement `LocalFileSystemSource` against it now. D1 SharePoint will implement the same protocol later. This mirrors the existing `TextExtractor` Protocol in `extraction.py` (structural typing, no shared base class to inherit).

Enumeration behaviour for `LocalFileSystemSource`:

- A single file yields itself; a directory is walked **recursively** and results are **sorted** for deterministic, reproducible output (fits the spec's < 100-files/run scale).
- "Supported" is defined by reusing `extraction.supported_suffixes()` — there is no second suffix list to keep in sync.
- An unsupported file type is **skipped with a `WARNING`** (stdlib `logging`), in both the directory and single-file cases, so a run over a mixed directory proceeds instead of aborting. This pins the spec TBD to **skipped-with-warning**.
- A missing path, or one that is neither a file nor a directory, raises `SourceError` (a new `AppError` subclass) — an absent source is distinct from an unsupported *type*.

## Alternatives

- **A plain `enumerate_documents(path)` function, no protocol** — less code today, but leaves the local/SharePoint seam unnamed; the CLI and D1 would have to invent the abstraction later, likely reshaping A3's call sites. Rejected: the second source is already specified, so this is a known need, not speculation.
- **Erroring on any unsupported file at enumeration** — a single stray `.txt` in a directory would abort the whole run. Rejected as too brittle for batch use. Direct extraction still errors (ADR-0006/0009); only enumeration skips-with-warning.
- **A shared abstract base class instead of a Protocol** — heavier, forces an inheritance relationship, and diverges from the `TextExtractor` Protocol already established in this codebase. Rejected for consistency.

## Tradeoffs

- **Gain:** one interface for every source; the CLI depends only on `DocumentSource`; adding SharePoint (D1) is a new implementation, not a rewrite. Enumeration is resilient to incidental non-document files.
- **Give up:** a small amount of structure (a Protocol + class) before the second implementation exists; skip-with-warning means a mistyped single-file path yields nothing rather than raising — surfaced only via the `WARNING` log.

## Consequences

- New module `src/sources.py` (`DocumentSource`, `LocalFileSystemSource`) and a new `SourceError(AppError)` in `src/errors.py`.
- `spec/spec.md`'s unsupported-file acceptance criterion is pinned: skipped-with-warning at the source, `UnsupportedFormatError` at extraction.
- D1 SharePoint ([ADR-0007](0007-sharepoint-app-only-auth.md)) implements `DocumentSource`; C1 ([ADR-0003](0003-cli-batch-interface.md)) selects a source and consumes the protocol.
- Reusing `supported_suffixes()` keeps the enumeration filter and the extractor registry in lockstep — registering a format stays a single change in `extraction.py`.
