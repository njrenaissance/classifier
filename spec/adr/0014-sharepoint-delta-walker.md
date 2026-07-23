# ADR-0014 — Incremental, resumable SharePoint ingestion (delta walker)

Status: accepted

> The change-detection hash field below (`file.hashes` "SHA-256") is refined by [ADR-0017](0017-graph-content-hash-field.md): the client reads `quickXorHash` first (what SharePoint / OneDrive-for-Business return), falling back to `sha256Hash`/`crc32Hash`. The rest of this ADR stands.

## Context

The walker ([ADR-0012](0012-cloud-two-job-pipeline.md)) enumerates a SharePoint document library via Microsoft Graph ([ADR-0007](0007-sharepoint-app-only-auth.md)) and enqueues work for changed files. At scale the first enumeration of thousands of files won't finish in one scheduled slot, and re-walking everything every run is wasteful and would re-enqueue unchanged files. Graph exposes **delta queries** (`/delta`) that paginate via `@odata.nextLink` and terminate with an `@odata.deltaLink` token usable to fetch only subsequent changes. The walker must be **resumable** (survive being cut off mid-walk) and **idempotent** (never enqueue a file already in flight or unchanged). This refines the uniform-source seam ([ADR-0010](0010-uniform-document-source.md)) for the SharePoint implementation.

## Decision

Walk with Graph **delta queries** under a configurable **time budget** (default 10 min), tracking position in `sync_state` with a **two-token design**:

- `delta_token` (`@odata.deltaLink`) — set only when a walk **completes**; the start point for the next incremental sync.
- `resume_token` (`@odata.nextLink`) — set when a walk is **interrupted** (budget exhausted mid-pagination); the exact page to resume from.
- Start-URL precedence: `resume_token` > `delta_token` > initial full-enumeration URL. `walk_status` ∈ {`idle`, `walking`, `interrupted`, `completed`}.

Per driveItem: derive `matter_folder` from `parentReference.path` (the first segment under `/Matters/`); files outside `/Matters/` are UPSERTed `status='skipped'` and never enqueued. **Change detection** compares Graph's `file.hashes` (SHA-256) against the stored `content_hash`. Enqueue rules keyed on `(sync_state_id, drive_item_id)`:

- in-flight (`status IN ('queued','processing')`) → skip;
- existing row + hash unchanged → skip;
- hash changed → rotate `previous_hash`/`content_hash`, set `status='queued'`, enqueue;
- new file → insert `status='queued'`, enqueue.

`delta_token` is stored **only on completion**, so changes that land during an interrupted run are still captured by the eventual completing walk. Re-classification is a `status='pending'` reset that the next walk re-enqueues through the same path (preserving any manual `classification_override`).

## Alternatives

- **Full re-walk each run** — simplest, but O(all files) every time and re-enqueues unchanged files; unfit at scale. Rejected.
- **Single delta token, no resume token** — loses position when a walk is interrupted mid-pagination, forcing a restart of that sync. Rejected: first-run enumeration spans multiple slots.
- **`graph_modified_at` timestamp instead of content hash** — cheaper, but a touched-but-unchanged file (metadata edit, re-sync) would re-classify needlessly; the content hash is the precise change signal. Rejected as the primary signal (timestamp kept as a secondary field).
- **Store `deltaLink` on interruption** — would advance the sync point past unprocessed changes, silently dropping them. Explicitly rejected.

## Tradeoffs

- **Gain:** incremental after the first pass (deltas finish in seconds); resumable across scheduled slots; idempotent enqueue (no duplicate messages, no lost pages); re-classification reuses the new-file path.
- **Give up:** more state and branching than a stateless walk; correctness depends on Graph returning stable `drive_item_id`s and populated `file.hashes` — documented assumptions to verify against the real tenant.

## Consequences

- New modules `src/graph_client.py` (delta pagination, path/hash parsing) and `src/walker.py` (budgeted loop, enqueue decisions); `sync_state` + `documents` schema per [ADR-0013](0013-postgresql-state-store.md).
- The queue-message contract carries `document_id`, `sync_state_id`, `drive_id`, `drive_item_id`, `file_name`, `mime_type`, `matter_folder`, `content_hash`, `enqueued_at` — enough for the classifier to act without a DB read; retry count is Azure Queue's `dequeueCount`, not a message field.
- Graph app registration + exact scopes (`Sites.Read.All`, `Files.Read.All`) remain the setup work flagged in [ADR-0007](0007-sharepoint-app-only-auth.md).
- The `_root` / non-`Matters` skip rule and matter-folder parsing are library-layout assumptions; a different library layout is a config/ADR change, not code sprinkled with special cases.
