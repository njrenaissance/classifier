# ADR-0020 — Local-filesystem source for the two-job pipeline

Status: accepted

## Context

The v2 pipeline (ADR-0012) is two jobs — a **walker** (producer) and a **processor**
(consumer) — decoupled by an Azure Queue. Today both bind directly to Microsoft Graph:
the walker enumerates via `GraphClient.iter_delta_pages(drive_id, …)` (a delta walk keyed
on `SyncState` resume/delta tokens), the processor retrieves via
`GraphClient.download(drive_id, drive_item_id)` + `fetch_content_hash(…)`, and the queue
`Message` is Graph-keyed (`drive_id` + `drive_item_id`). ADR-0012 itself recorded the cost
of this: *"local end-to-end no longer exercises the full production path (the CLI covers
the core, not the walker/queue seam)."*

The `DocumentSource` abstraction (ADR-0010) with `LocalFileSystemSource` already exists,
but it is consumed **only by the v1 CLI** (`src/main.py`) — the v2 jobs bypass it. So
"point the pipeline at a mounted filesystem" is a feature, not a config flip: it needs a
filesystem-backed **producer**, a filesystem **retrieval** seam in the processor, and a
**message contract** that can carry a source-neutral locator (a local file has no
`drive_id`/`drive_item_id`).

Two forces make this worth doing now. First, live-fire integration testing wants to run
the *real* walker → *real* queue → *real* PostgreSQL → *real* classification path without
any Graph/SharePoint stubs (issue #61, motivated by ADR-0019/#59). Second, it generalises
the v2 jobs back toward ADR-0010's uniform-source idea. The cloud pipeline is not yet
deployed, so there is no live `sync_state`/`documents` data whose shape or column names
must be preserved.

## Decision

Add a **source-mode toggle** — `CLASSIFIER_SOURCE=sharepoint|filesystem` (default
`sharepoint`) — and, for the filesystem mode, a filesystem producer and a filesystem
retrieval seam that reuse the existing enqueue/idempotency logic and the ADR-0010
enumeration. Four parts:

1. **Two narrow seams over one shared enqueue core.** The delta `Walker` is tightly
   coupled to `SyncState` tokens and a per-run page budget; the filesystem producer does
   **full re-enumeration each run** (a filesystem has no delta token). Rather than force
   both into one class, extract the source-neutral decision/persistence/enqueue logic
   (the `queued`-row UPSERT, the new/changed/in-flight/pending enqueue rules, hash
   rotation into `previous_hash`, and the **commit-before-enqueue** ordering) into a
   shared `Enqueuer` collaborator keyed on a source-neutral `DocumentCandidate`. Both the
   Graph `Walker` and a new `FilesystemWalker` delegate to it, so idempotency behaves
   identically regardless of source. On the retrieval side, the processor depends on a
   `ContentSource` protocol (`fetch_content_hash(message)`, `download(message)`) with a
   Graph implementation and a filesystem implementation, instead of binding to the
   concrete `GraphClient`.

2. **Filesystem identity reuses the existing schema — no migration.** A local file maps
   onto the current `(sync_state_id, drive_item_id)` model with the Graph-named columns
   carrying source-neutral values:

   | Column | SharePoint | Filesystem |
   |---|---|---|
   | `SyncState.drive_id` (UNIQUE) | Graph drive id | synthetic `filesystem:<abs-root>` |
   | `SyncState.delta_token` / `resume_token` | delta / next links | `NULL` (full re-enumeration) |
   | `Document.drive_item_id` | Graph item id | file path **relative to root** (POSIX) |
   | `Document.content_hash` | `quickXorHash` etc. (ADR-0017) | `sha256` hex of file bytes |
   | `Document.mime_type` | `file.mimeType` | derived from the file suffix |
   | `Document.folder_path` | `parentReference.path` (ADR-0018) | parent path relative to root |

   The UNIQUE `(sync_state_id, drive_item_id)` UPSERT key works unchanged because the
   relative path is stable across runs. There is **one** definition of the filesystem hash
   algorithm (`hash_bytes`, `sha256`), imported by both the producer (enqueue-time hash)
   and the retrieval seam (processor re-check hash), so the existing "hash mismatch → skip,
   walker re-enqueues" contract holds for the filesystem path too.

3. **Widen the queue `Message` with a `source` discriminator, reusing `drive_item_id` as
   the locator.** Add `source: MessageSource` (`sharepoint` | `filesystem`, default
   `sharepoint` so pre-existing messages still parse). `drive_item_id` doubles as the
   source-neutral locator — a Graph item id for SharePoint, a relative path for filesystem
   — which the `FilesystemContentSource` resolves against the configured mount root. No
   separate `path` field is added: `drive_item_id` *is already* the per-source identity and
   the UPSERT key, so a second locator field would duplicate it and risk drift. The `source`
   field makes a message self-describing (a processor can fail fast on a misconfigured
   mixed queue), while the seam is still selected from config.

4. **A manual, local `infra/` docker-compose live-fire stack.** PostgreSQL + Azurite +
   `classifier:ci` as a **one-time migration step** (`alembic upgrade head`), a **walker**,
   and a **processor**, with a host directory of sample documents mounted read-only at
   `/data` and `CLASSIFIER_SOURCE=filesystem`. The migration step runs **before** and is a
   `depends_on` of the walker/processor (jobs never self-migrate — schema is
   Alembic-managed, fix-forward only; ADR-0013 / CLAUDE.md). Classification makes **real**
   LLM calls (needs a valid `ANTHROPIC_API_KEY` / Foundry), so the stack is manual/local
   and **not** run in CI — it costs money per run.

## Alternatives

- **Force the filesystem source into the existing `Walker`** (a source flag inside the
  delta loop) — entangles full-re-enumeration logic with the `SyncState` token/budget
  machinery that the filesystem path never uses, muddying the one class that must stay
  correct for the (deployed-first) SharePoint path. Rejected in favour of a separate
  producer over a shared `Enqueuer`.
- **Rename the Graph-flavoured columns to source-neutral names** (`drive_id`→`source_key`,
  `drive_item_id`→`locator`) via a forward migration — cleaner names, but ripples through
  `db.py`, `models.py`, `writer.py`, `walker.py`, `processor.py`, every migration's
  reasoning, and every test, for cosmetic gain on a not-yet-deployed schema. Rejected;
  recorded here as the deliberate tradeoff, and available as its own ADR later if the
  naming becomes confusing.
- **Add an explicit `path` locator field to `Message`** alongside the discriminator —
  more self-describing on the wire, but redundant with `drive_item_id` (which already
  carries the relative path) and a second field to keep in sync. Rejected.
- **A separate filesystem message/queue contract** — a clean-room second pipeline avoids
  overloading the Graph fields, but duplicates the queue seam, the enqueue rules, and the
  processor, defeating the "one pipeline, swappable source" goal. Rejected.

## Tradeoffs

- **Gain:** the full walker → queue → PostgreSQL → classification path is exercisable
  locally against real files with no Graph/SharePoint stubs; the idempotency core is
  shared, so both sources behave identically; no schema migration; the SharePoint path is
  untouched (default `sharepoint`, backward-compatible message wire).
- **Give up:** the Graph-named columns (`drive_id`, `drive_item_id`) now carry
  source-neutral values in filesystem mode — a documented semantic overload rather than
  faithful names. Correctness of the filesystem re-check depends on the producer and
  processor computing the *same* `sha256` over the *same* bytes (guaranteed by the single
  `hash_bytes`). The live-fire stack spends real LLM budget, so it is manual, not CI.

## Consequences

- **New modules:** `src/enqueuer.py` (`Enqueuer`, `DocumentCandidate` — extracted
  behaviour-preservingly from `Walker`), `src/content_source.py` (`ContentSource` protocol,
  `GraphContentSource`, `FilesystemContentSource`, `hash_bytes`), `src/filesystem_walker.py`
  (`FilesystemWalker`, reusing `sources.LocalFileSystemSource.documents()`).
- **`src/models.py`** adds `MessageSource` and `Message.source` (default `sharepoint`).
- **`src/config.py`** adds a top-level `source` scalar (`CLASSIFIER_SOURCE`, like
  `provider`) and a `FilesystemSettings` section (`CLASSIFIER__FILESYSTEM_ROOT`);
  `walker.run` / `processor.run` branch on `settings.source`, and the filesystem branch
  never constructs a `GraphClient` nor requires `CLASSIFIER__WALKER_DRIVE_ID`.
- **`src/extraction.py`** adds `mime_type_for_suffix()` (reverse of `_MIME_TO_SUFFIX`) so
  the suffix↔MIME mapping stays owned in one module; the filesystem producer derives
  `mime_type` from the suffix and the processor's `extract_text_from_bytes(data, mime_type)`
  is reused unchanged.
- **`src/processor.py`** binds to `ContentSource` instead of `GraphClient`; **`src/db.py`
  is unchanged** (no migration).
- **`infra/`** is added: `docker-compose.yml` (postgres + azurite + one-shot `migrate` +
  walker + processor, sample docs mounted), a `README.md` live-fire runbook, and
  `sample-docs/`. Two operational caveats are documented there: Azurite does **not**
  auto-create the queue (an idempotent create-if-not-exists init step is needed), and the
  processor is single-shot (`run_once`, KEDA-per-message in production) so draining N files
  locally is a documented `docker compose run --rm processor` loop — **not** a polling loop
  baked into the production entrypoint. The same one-time migration init step is also
  required for E8/#46 (Azure IaC).
- **Tests** (TDD, agreed before implementation): unit tests for the filesystem producer
  (enumerate → hash → upsert → enqueue; idempotent on unchanged hash) and filesystem
  retrieval (path → bytes), boundaries mocked; one `integration` test on testcontainers
  PostgreSQL + Azurite where the walker enqueues + writes rows and the processor consumes +
  UPSERTs with Graph never touched and the voter faked; and a documented manual live-fire
  run with real verdicts. The SharePoint (`sharepoint`) path stays green.
- `spec/spec.md`'s ingestion note and `README.md` are updated to describe the selectable
  source and the live-fire stack.
