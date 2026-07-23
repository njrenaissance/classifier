# ADR-0018 — Store the driveItem folder path, not a derived matter

Status: accepted

Supersedes the matter-folder derivation and `matter_folder` storage of [ADR-0014](0014-sharepoint-delta-walker.md).

## Context

ADR-0014 had the walker derive a `matter_folder` — the first path segment beneath `/Matters/` in a driveItem's `parentReference.path` (e.g. `.../root:/Matters/Smith-2026-001/Discovery` → `Smith-2026-001`) — and persist it on the `documents` row. In practice this derivation is brittle: it hard-codes a `/Matters/` library layout, has to decode percent-encoded path segments correctly, and needs a sentinel for items that sit outside a matter. It also stores information the classifier does not use — classification is by **document type**, not by which matter/case a file belongs to.

## Decision

Do **not** derive or store a matter/case. Store the driveItem's raw `parentReference.path` verbatim in a `documents.folder_path` column instead. A matter (or any other grouping) can be reconstructed from that path later if a use for it arises, but nothing in the classification pipeline depends on it. The Graph client exposes a single pure helper, `folder_path(item)`, that returns the raw path (or `None`); the previous `matter_folder(item)` parser and its `_root` sentinel are removed.

Whether ingestion is *scoped* to a particular library subtree (e.g. `/Matters/`) remains a walker concern (ADR-0014's enqueue/skip logic); this ADR only changes what is **parsed and persisted per document**, replacing a derived, lossy field with the faithful source path.

## Alternatives

- **Keep deriving `matter_folder` (ADR-0014 as written)** — couples storage to a specific folder layout and to correct path decoding, for a value the classifier never reads. Rejected as brittle.
- **Store both the path and a derived matter** — redundant: the matter is a pure function of the path, so deriving it eagerly buys nothing and re-introduces the brittleness. Rejected.
- **Store nothing about location** — loses the ability to ever group or audit by folder/matter. Rejected; the raw path is cheap to keep and fully general.

## Tradeoffs

- **Gain:** no fragile per-segment parsing or layout assumption in the hot path; the faithful source path is retained; classification stays purely document-type driven.
- **Give up:** querying "documents in matter X" now means parsing `folder_path` at read time rather than filtering an indexed column — acceptable, since that access pattern is not part of the pipeline and may never be needed.

## Consequences

- `src/graph_client.py` replaces `matter_folder()` with `folder_path()`; `src/db.py` replaces the `documents.matter_folder` column with `documents.folder_path`, applied by forward migration `0002_folder_path` (drop `matter_folder`, add `folder_path`).
- `folder_path` is persisted on the `documents` row by the walker — it is a database column, not a queue-message field. The processor classifies by document type and needs no folder in the message, so `models.Message` carries none (matching the queue-message model, which drops `matter_folder` without adding `folder_path`).
- ADR-0014's `_root` / non-`Matters` matter-derivation clause is superseded; a pointer is added at the top of ADR-0014.
