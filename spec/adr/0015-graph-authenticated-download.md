# ADR-0015 — Graph-authenticated on-demand file download

Status: accepted

## Context

The classifier job ([ADR-0012](0012-cloud-two-job-pipeline.md)) receives a work item identifying a SharePoint file (`drive_id` + `drive_item_id`) and must obtain its bytes to extract text. Files stay in SharePoint — the pipeline copies nothing to blob storage. Downloading from Graph requires an authenticated call, and there is no interactive user at run time (app-only, [ADR-0007](0007-sharepoint-app-only-auth.md)). The extraction strategies ([ADR-0006](0006-text-extraction-per-format-libs.md)) currently take a filesystem `Path`.

## Decision

The classifier **downloads on demand, authenticated to Graph app-only**: acquire an application token (managed identity in production; client-credentials locally / until a federated credential is configured), then `GET /drives/{drive_id}/items/{drive_item_id}/content`, streaming the bytes **into memory** (no local copy beyond a transient buffer). Optionally verify the current Graph `content_hash` matches the work item's; on mismatch, skip and let the walker re-enqueue.

Extraction is adapted to accept an **in-memory byte stream** (`BytesIO`) alongside the existing `Path` API, so `pypdf` / `python-docx` read the downloaded bytes directly and the extractor registry / suffix dispatch is unchanged (`mime_type` from the work item selects the strategy without a first download).

## Alternatives

- **Pre-signed / anonymous download URL in the message** — simplest container (a plain HTTPS GET, no Graph SDK), but Graph pre-authenticated download URLs are short-lived, and minting one shifts an authenticated Graph call into the walker anyway; it also weakens the "files never leave SharePoint's access control" posture. Rejected in favour of authenticated fetch at use time.
- **Walker downloads and passes bytes through the queue** — Azure Queue messages cap at 64 KB; documents far exceed that, and it couples the walker to file I/O. Rejected.
- **Download to a temp file, keep the `Path`-only extractor API** — avoids the `BytesIO` adapter, but adds disk I/O and cleanup for no benefit in a stateless container. Rejected; the in-memory adapter is small and keeps the classifier stateless.

## Tradeoffs

- **Gain:** files never leave SharePoint's access boundary; the classifier stays stateless (no disk); one auth model (app-only) for both walk and download; the hash re-check guards against stale work items.
- **Give up:** every classified file costs an authenticated Graph download at processing time; large documents hold bytes in memory (bounded by ACA memory and the over-context truncation of [ADR-0008](0008-prompt-structured-output.md)).

## Consequences

- `src/graph_client.py` gains an authenticated `download(drive_id, drive_item_id) -> bytes`; `src/extraction.py` gains a stream-based entry point without changing the strategy registry.
- The managed identity needs Graph application permissions; local dev uses a client secret from Key Vault until a federated credential replaces it ([ADR-0007](0007-sharepoint-app-only-auth.md)).
- Over-context handling (truncation) for very large downloads is pinned during implementation, as already flagged in [ADR-0008](0008-prompt-structured-output.md).
