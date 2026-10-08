---
type: Workflow
title: Filesystem Pipeline — Local Enumeration and Classification
description: Two-job pipeline running against a mounted local directory instead of SharePoint, enabling local end-to-end testing of walker, queue, and processor without Graph dependencies.
tags: [workflow, local, filesystem, testing, integration, producer-consumer, ADR-0020]
verified:
  - by: openwiki/0.4.3
    at: 2026-08-28T19:49:26.700Z
sources:
  - id: openwiki-source-ca0a86464b12e2013094f726
    resource: repo://infra/docker-compose.yml
  - id: openwiki-source-862443b88cee5adeb9e4ba55
    resource: repo://infra/README.md
  - id: openwiki-source-dc0db67ef5199acd3cec29fa
    resource: repo://spec/adr/0020-local-filesystem-source.md
  - id: openwiki-source-d502c275990c6476221bf080
    resource: repo://src/config.py
  - id: openwiki-source-8d1a30a0ada8a519a416e3a0
    resource: repo://src/content_source.py
  - id: openwiki-source-fbc7db9240740e6fed706532
    resource: repo://src/enqueuer.py
  - id: openwiki-source-a2690fbc7dfdd4c5929ecd6a
    resource: repo://src/filesystem_walker.py
  - id: openwiki-source-61a4c09bfce6828071a1f7dc
    resource: repo://src/models.py
  - id: openwiki-source-d6651d3bc51203d33893f15c
    resource: repo://src/processor.py
  - id: openwiki-source-0b08ad2ade4feed2ba3e8fa7
    resource: repo://src/walker.py
  - id: openwiki-source-28c21d7c5df204edb1750fd6
    resource: repo://tests/test_filesystem_pipeline_integration.py
generated: { by: "openwiki/0.4.3", at: "2026-08-28T19:49:26.700Z" }
---

# Filesystem Pipeline Workflow

The filesystem pipeline is an alternative to the SharePoint/Graph-based [cloud pipeline](/openwiki/workflows/cloud-pipeline.md) that targets a mounted local directory instead of Microsoft SharePoint. It exercises the *real* two-job architecture (walker → queue → processor → database) end-to-end without external dependencies, enabling local integration testing and no-cost smoke tests.

**Status:** ADR-0020 accepted. The live-fire docker-compose stack runs manually; the automated no-cost path is the pytest integration test.

## When to Use Filesystem Source

| Scenario | Use Filesystem | Use SharePoint |
|---|---|---|
| **Local development** of walker/processor logic | ✅ Real files + real queue + real database | ❌ Requires Graph credentials |
| **Integration testing** (no LLM cost) | ✅ Full pipeline, faked voter, real I/O | ❌ Requires Graph credentials |
| **No-cost smoke tests** of message contract + seams | ✅ `pytest -m integration` | ❌ Requires Graph credentials |
| **Live classification** (real LLM verdicts) | ⚠️ `infra/docker-compose.yml` only | ✅ Production path |
| **SharePoint enumeration** (delta-walk, resume tokens) | ❌ Filesystem has no delta | ✅ Production path |

**Key constraint:** Filesystem re-enumeration is **always full** — there is no delta token or resume mechanism. The walker's idempotency comes entirely from content-hash comparison, not from resumable pagination.

## Architecture Overview

<!-- openwiki: mermaid parse failed and this diagram was converted to a text fence so it does not break rendering. Fix the diagram source and restore the mermaid fence. Parser error: Heuristic: an unescaped angle bracket inside a label breaks rendering; rephrase the label. -->
```text
graph LR
    Root["Mounted Directory<br/>(read-only)"]
    
    subgraph Filesystem["Filesystem Path (ADR-0020)"]
        Walker["FilesystemWalker<br/>(full re-enumeration)"]
        Enqueuer["Enqueuer<br/>(shared)"]
        Queue["Azure Queue<br/>(Azurite locally)"]
        Processor["Processor<br/>(run_once)"]
        Source["FilesystemContentSource<br/>(resolve + read)"]
        DB["PostgreSQL<br/>(documents table)"]
    end
    
    Root -->|enumerate| Walker
    Walker -->|hash + path| Enqueuer
    Enqueuer -->|UPSERT queued| DB
    Enqueuer -->|enqueue message| Queue
    Queue -->|dequeue| Processor
    Processor -->|source.fetch_hash()<br/>source.download()| Source
    Source -->|resolve path| Root
    Processor -->|UPSERT result| DB
    
    style Root fill:#e8f4f8
    style Filesystem fill:#f0f8e8
```

The workflow is identical to the cloud pipeline in *shape* (producer → queue → consumer) but differs in:
- **Producer:** Full directory re-enumeration every run (no delta token) instead of incremental Graph delta walk
- **Enumeration:** File paths from the mounted tree instead of driveItems from Graph
- **Identity:** Root-relative POSIX paths stored in `drive_item_id` instead of Graph item IDs
- **Idempotency:** Content-hash matching instead of sync tokens
- **Retrieval:** Direct disk I/O instead of Graph download

## How FilesystemWalker Works

**Entry point:** `python -m walker` with `CLASSIFIER_SOURCE=filesystem`

### Single Walk Lifecycle

```
1. Load or create SyncState row (keyed "filesystem:<root-path>")
   ↓
2. Mark walk as "walking"
   ↓
3. Enumerate LocalFileSystemSource(root).documents()
   ├─ Recursively walk all supported files (.pdf, .docx, etc.)
   ├─ Hash each file's bytes via sha256
   ├─ Build DocumentCandidate (drive_item_id = relative path, content_hash = sha256)
   ↓
4. For each candidate, call Enqueuer.enqueue_if_needed()
   ├─ Lookup existing Document row by (sync_state_id, drive_item_id)
   ├─ Apply idempotency decision: skip unchanged, re-enqueue changed, enqueue new
   ├─ UPSERT Document row to "queued" status
   ├─ Commit (this happens BEFORE the queue send)
   ├─ Enqueue Message to Azure Queue
   ↓
5. Mark walk as "completed"
6. Stamp last_synced_at
7. Exit
```

**Key properties:**

- **No delta token:** The `SyncState.delta_token` and `SyncState.resume_token` remain `NULL`. A filesystem has no change feed, so every run is a fresh enumeration.
- **Full re-enumeration:** Every run walks the entire tree from root. Idempotency (skipping unchanged files) comes from hashing, not pagination state.
- **Deterministic hash:** Each file is hashed via the shared `hash_bytes(data: bytes) -> str` function (SHA-256 hex), imported by both the walker and the processor's `FilesystemContentSource`.
- **Relative path identity:** A file's `drive_item_id` is its path *relative to the root* in POSIX format (e.g., `"alpha.pdf"`, `"sub/beta.docx"`), stable across runs and used by the processor as the locator to re-open the file.

### Configuration

The filesystem walker is selected and configured via environment:

```bash
export CLASSIFIER_SOURCE=filesystem
export CLASSIFIER__FILESYSTEM_ROOT=/data        # The mount root
export CLASSIFIER__DATABASE_URL=postgresql://...
export CLASSIFIER__QUEUE_NAME=classifier-work-items
export CLASSIFIER__QUEUE_CONNECTION_STRING=...  # Azurite in local testing
```

No Graph credentials are required. The walker never constructs a `GraphClient`.

## How Enqueuer Works (Shared)

**Location:** `src/enqueuer.py`

The `Enqueuer` is the source-neutral core of both producers — the SharePoint `Walker` (ADR-0014) and the `FilesystemWalker` (ADR-0020) delegate their enqueue decisions to it, ensuring byte-for-byte identical idempotency logic regardless of source.

### Enqueue Decision Logic

For each `DocumentCandidate`:

1. **Lookup existing `Document` row** by `(sync_state_id, drive_item_id)`
2. **Apply decision:**
   - **New file** (no row) → enqueue
   - **In-flight** (status = `queued` or `processing`) → skip (prevent duplicate work)
   - **Status = `pending`** (manual re-classification request) → re-enqueue
   - **Content hash unchanged** → skip
   - **Content hash changed** → rotate old hash to `previous_hash`, re-enqueue
3. **On enqueue:** UPSERT the row to `queued` status, then enqueue the message

### Commit-Before-Enqueue Ordering

```python
# 1. Update Document row and commit
self._session.flush()      # assign document.id
document.status = DocumentStatus.queued
self._session.commit()     # persists the row BEFORE the queue send

# 2. Build message (reads from just-committed row)
message = Message(document_id=document.id, drive_item_id=document.drive_item_id, ...)

# 3. Enqueue to Azure Queue
self._queue.enqueue(message)
```

This ordering is critical: if the queue send fails (rare), the row stays in `queued` status and the next walk's in-flight check skips it, preventing duplicates. A send failure leaves an at-most-once gap, recoverable via a `pending` reset.

## Synthetic SyncState Row for Filesystem

The database schema uses `(sync_state_id, drive_item_id)` as the unique key for documents. The filesystem walker creates a **synthetic** `SyncState` row to satisfy this foreign key:

| Column | SharePoint | Filesystem |
|---|---|---|
| `SyncState.drive_id` (UNIQUE) | Graph drive UUID | `"filesystem:/data"` (absolute root path) |
| `SyncState.delta_token` | `@odata.deltaLink` from Graph | `NULL` (no delta) |
| `SyncState.resume_token` | `@odata.nextLink` on interrupt | `NULL` (full re-enumeration always) |
| `SyncState.walk_status` | `completed` / `interrupted` | `completed` (always, no budget) |
| `Document.drive_item_id` | Graph item ID | Relative POSIX path (e.g., `"sub/file.pdf"`) |
| `Document.content_hash` | Graph `quickXorHash` | SHA-256 hex of file bytes |
| `Document.mime_type` | `file.mimeType` from Graph | Derived from file suffix |
| `Document.folder_path` | `parentReference.path` | Parent path relative to root |

This mapping allows the entire `documents` table and UPSERT logic to be source-agnostic.

## How FilesystemContentSource Re-Checks Hash

**Location:** `src/content_source.py`

The processor's retrieval seam for filesystem messages is `FilesystemContentSource`, which re-validates each file before processing:

```python
class FilesystemContentSource:
    def fetch_content_hash(self, message: Message) -> str | None:
        """Return the file's *current* hash (re-check before classification)."""
        return hash_bytes(self.download(message))
    
    def download(self, message: Message) -> bytes:
        """Resolve the relative path and read bytes from disk."""
        path = self._resolve(message.drive_item_id)  # e.g., "sub/file.pdf"
        return path.read_bytes()
    
    def _resolve(self, locator: str) -> Path:
        """Reject absolute paths and escape attempts."""
        candidate = Path(locator)
        if candidate.is_absolute():
            raise SourceError("Must be relative")
        resolved = (self._root / candidate).resolve()
        if not _is_inside(resolved, self._root):
            raise SourceError("Escapes mount root")
        return resolved
```

### Hash Mismatch Handling

If the processor's re-checked hash does **not** match the walker's enqueued hash:

1. **Processor skips the file** — mark as `skipped` with reason "content_hash mismatch; walker will re-enqueue"
2. **Message is deleted** from the queue
3. **Next walk** re-enumerates the file, detects the (now-different) hash, and re-enqueues it

This contract works identically for both SharePoint and filesystem sources. The single `hash_bytes()` function is imported by both the walker and the processor, ensuring they always agree on the hash algorithm.

## Message Contract

Queue messages carry a `source` discriminator (ADR-0020) to self-describe their origin:

```python
class Message(BaseModel):
    source: MessageSource = MessageSource.sharepoint  # "sharepoint" or "filesystem"
    drive_id: str  # Graph drive ID or "filesystem:/absolute/root"
    drive_item_id: str  # Graph item ID or "relative/path.pdf"
    document_id: int
    sync_state_id: int
    content_hash: str
    mime_type: str
    file_name: str
    enqueued_at: AwareDatetime
```

- **`source`** defaults to `sharepoint` so pre-existing messages (before ADR-0020) still parse correctly
- **`drive_item_id`** doubles as the source-neutral locator: for filesystem, it is a relative path; for SharePoint, it is a Graph item ID
- **`drive_id`** is synthetic for filesystem (`"filesystem:<root>"`) but never used by `FilesystemContentSource` — the processor selects its retrieval seam from config, and the seam reads whichever fields it needs

## Configuration-Driven Source Selection

Both `walker` and `processor` jobs select their source at runtime via `CLASSIFIER_SOURCE`:

### Walker

```python
# src/walker.py
def run(argv: list[str]) -> int:
    settings = get_settings()
    if settings.source == "filesystem":
        status, label = _run_filesystem_walk(settings)
    else:
        status, label = _run_sharepoint_walk(settings)
    return 0 if status else 1
```

### Processor

```python
# src/processor.py
def run(argv: list[str]) -> int:
    settings = get_settings()
    if settings.source == "filesystem":
        _process_via_filesystem(settings, voter)
    else:
        _process_via_sharepoint(voter)
    return 0 or 1
```

No `if` statements appear in the core logic — the seam is wired at entry time.

## Live-Fire Local Stack

The `infra/docker-compose.yml` stack brings up a **manual, local** pipeline that makes *real* LLM calls (costs money per run). It is **not** run in CI.

### What's in the Stack

| Service | Role |
|---|---|
| `postgres` | PostgreSQL 16, health-checked |
| `azurite` | Azure Queue Storage emulator (queue only) |
| `migrate` | One-shot `alembic upgrade head`; the walker/processor depend on it completing (jobs never self-migrate) |
| `queue-init` | One-shot idempotent queue creation (Azurite does not auto-create) |
| `walker` | `python -m walker` with `CLASSIFIER_SOURCE=filesystem`; enumerates `/data`, hashes, enqueues |
| `processor` | `python -m processor` with `CLASSIFIER_SOURCE=filesystem`; **single-shot** — processes one message and exits |

### Prerequisites

1. **Build the Docker image:**
   ```bash
   docker build -t classifier:ci .
   ```

2. **Export your API key** (PowerShell):
   ```powershell
   $env:ANTHROPIC_API_KEY = "sk-ant-..."
   ```

3. **Choose the documents** to classify. Either drop files into `sample-docs/`:
   ```bash
   cp /path/to/*.pdf ./sample-docs/
   ```
   Or point at your own folder:
   ```powershell
   $env:SAMPLE_DOCS = "C:\Users\jon_m\testdocs"
   ```

### Running It

```bash
cd infra
docker compose -f docker-compose.yml up --abort-on-container-exit
```

**Order of events:**
1. `postgres` becomes healthy
2. `migrate` applies the schema
3. `queue-init` creates the queue
4. `walker` enumerates `/data`, hashes files, enqueues messages
5. `processor` (single-shot) classifies one message with a real LLM verdict

### Draining the Queue

The processor is **single-shot by design** — it processes one message and exits. To classify every queued document, re-run the processor in a loop:

```bash
# After the initial `up` has populated the queue
while ($true) {
    docker compose -f infra/docker-compose.yml run --rm processor
    if ($LASTEXITCODE -ne 0) { break }
}
```

Each invocation processes one message and returns `0`; when the queue is empty, the run logs `Queue empty; nothing to process`.

### Inspecting Results

```bash
docker compose -f infra/docker-compose.yml exec postgres \
  psql -U classifier -d classifier -c \
  "select drive_item_id, status, category, confidence from documents order by drive_item_id;"
```

The `processing_log` table holds the per-attempt audit trail (status, category, tokens, cost).

### Tearing Down

```bash
docker compose -f infra/docker-compose.yml down -v
```

The `-v` flag also removes Postgres and Azurite volumes for a clean next run.

## Automated Integration Test

For a **no-cost, automated** version of the same workflow (real files + real queue + real database, but faked voter), use pytest:

```bash
uv run pytest -m integration tests/test_filesystem_pipeline_integration.py
```

This test:
- Creates a real PostgreSQL via testcontainers (runs the Alembic migration)
- Creates a real Azurite queue endpoint
- Populates a temp directory with valid PDFs
- Runs the real `FilesystemWalker` → enqueues messages
- Runs the real `Processor` with `FilesystemContentSource` → extracts text + classifies (faked voter)
- Verifies all documents completed with correct classifications
- Cleans up containers and volumes

**Location:** `tests/test_filesystem_pipeline_integration.py`

It skips if Docker is unavailable.

## Code Examples

### Minimal Walker Run

```python
from pathlib import Path
from sqlalchemy.orm import Session
from filesystem_walker import FilesystemWalker
from message_queue import MessageQueue

root = Path("/data")
session: Session = ...
queue: MessageQueue = ...

walker = FilesystemWalker(session, queue, root)
status = walker.walk()
print(f"Walk completed: {status}")
```

### Minimal Processor Run

```python
from pathlib import Path
from content_source import FilesystemContentSource
from processor import Processor

root = Path("/data")
source = FilesystemContentSource(root)

processor = Processor(session, source, queue, voter, writer)
processor.run_once()  # process one message, exit
```

### Environment Configuration (Full Example)

```bash
# Source selection
export CLASSIFIER_SOURCE=filesystem

# Filesystem root
export CLASSIFIER__FILESYSTEM_ROOT=/mnt/documents

# Database (required for both jobs)
export CLASSIFIER__DATABASE_URL=postgresql+psycopg://user:pass@localhost/classifier

# Queue (required for both jobs)
export CLASSIFIER__QUEUE_NAME=classifier-work-items
export CLASSIFIER__QUEUE_CONNECTION_STRING=DefaultEndpointsProtocol=http;...

# Processor only (category file)
export CLASSIFIER__PROCESSOR_CATEGORY_FILE=/app/categories.md

# LLM provider (processor only, for real classifications)
export ANTHROPIC_API_KEY=sk-ant-...
```

Then:
```bash
python -m walker    # Full re-enumeration + enqueue
python -m processor # Classify one message (run in a loop to drain the queue)
```

## Related Concepts

- **[ADR-0020](/spec/adr/0020-local-filesystem-source.md)** — Architectural decision to add filesystem source mode
- **[Cloud Pipeline](/openwiki/workflows/cloud-pipeline.md)** — The SharePoint/Graph-based production path
- **[Document Sources and Pluggable Seams](/openwiki/architecture/sources-and-seams.md)** — Abstraction patterns for source-agnostic code
- **[Configuration](/openwiki/operations/configuration.md)** — Environment variable reference
- **ADR-0012** — Two-job cloud pipeline (producer-consumer via queue)
- **ADR-0014** — SharePoint delta walker and idempotency rules
- **ADR-0013** — Alembic-managed schema (jobs never self-migrate)

## Limitations and Future Work

- **No delta token:** Every run is a full re-enumeration. For large trees, this may be slow; consider time budgets or pagination if scalability becomes an issue.
- **Single-host only:** The mounted root must be present on the job's filesystem. Cross-machine NAS mounts work, but cross-cloud scenarios (S3, Azure Blob) would require a new source implementation.
- **Relative path stability:** Moving files within the tree changes their locator; a file moved from `old/file.pdf` to `new/file.pdf` will be treated as a new document (different drive_item_id) and re-classified.
