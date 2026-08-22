---
type: Workflow
title: Cloud Pipeline
description: Distributed two-job system — walker enumerates SharePoint, processor classifies via queued messages
resource: /src/walker.py
tags: [workflow, cloud, distribution, queue, sharepoint, producer-consumer]
---

# Cloud Pipeline Workflow

This page traces the journey of a document through the distributed two-job cloud pipeline: from SharePoint enumeration through classification and result persistence.

## High-Level Overview

```
Scheduler
    ↓
Walker (scheduled job)
    ├─ Delta-walk SharePoint via Graph
    ├─ Persist sync state (position)
    └─ Enqueue changed/new files
           ↓
      Azure Queue
      (work items)
           ↓
KEDA Scaler
(one replica
per message)
           ↓
Processor (queue-triggered job)
    ├─ Dequeue message
    ├─ Hash re-check (skip if unchanged)
    ├─ Download from SharePoint
    ├─ Extract text
    ├─ Classify (self-consistency)
    └─ UPSERT result to PostgreSQL
           ↓
PostgreSQL (durable state)
```

## Step 1: Walker — Scheduled SharePoint Enumeration

**Entry point:** `python -m walker` (scheduled Azure Container Apps job)

**Inputs:**
- SharePoint drive ID (configured)
- Library root path to enumerate (default: `/Matters`, configurable via `CLASSIFIER__WALKER_ROOT_PATH`)
- Time budget in seconds (default: 600, configurable via `CLASSIFIER__WALKER_TIME_BUDGET_SECONDS`)

### Walker Lifecycle

1. **Read sync state** — Query `SyncState` row for this drive; if missing, this is a fresh enumeration
2. **Determine start point** — Use `resume_token` if present (interrupted walk), else `delta_token` (incremental), else start fresh
3. **Delta walk** — Call Microsoft Graph `/delta` endpoint with the starting token; fetch items page by page within the time budget
4. **For each item** — Check if it's in scope (`root_path` filter applied at Graph level), extract folder path and content hash
5. **Enqueue decision** — For each in-scope item:
   - Skip if status is `queued` or `processing` (already in flight)
   - Skip if content hash is unchanged (no new work needed)
   - Re-queue if hash changed (document was modified); rotate old hash to `previous_hash`
   - If status is `pending` (manual re-classification request), always re-queue
   - Else, enqueue as new work
6. **On budget exhaustion** — Persist current `@odata.nextLink` as `resume_token`, mark walk `interrupted`, exit
7. **On completion** — Persist final `@odata.deltaLink` as `delta_token`, clear `resume_token`, mark walk `completed`, stamp `last_synced_at`, exit

**Key properties:**
- **Resumable:** Interrupted walks pick up from `resume_token` on the next scheduled run, so large first enumerations don't need to finish in one slot
- **Idempotent:** Tracks file content hash (`SHA-256` of bytes) and status to prevent duplicate classifications
- **Scoped:** Root path is enforced at the Graph delta level ([ADR-0019](../../spec/adr/0019-config-driven-walk-scope.md)), so the walk never sees out-of-scope items

**Design decisions:**
- [ADR-0012](../../spec/adr/0012-cloud-two-job-pipeline.md) — Two-job pipeline decoupled by queue
- [ADR-0014](../../spec/adr/0014-sharepoint-delta-walker.md) — Delta walk with resumable tokens
- [ADR-0019](../../spec/adr/0019-config-driven-walk-scope.md) — Scoped enumeration at Graph level

### Example: First Run (Fresh Enumeration)

```
1. No SyncState row exists
2. Start point: full enumeration
3. Graph /delta returns items in /Matters (scoped at API level)
4. For each file:
   - Calculate content_hash = SHA-256(bytes)
   - Insert Document row with status=queued
   - Enqueue Message(drive_item_id, folder_path, file_name, mime_type, content_hash)
5. On budget exhaustion (e.g., 10 min):
   - Persist resume_token
   - Mark walk interrupted
6. Next scheduled run:
   - Resume from resume_token
   - Continue pagination
7. When delta walk finishes:
   - Persist delta_token
   - Clear resume_token
   - Mark walk completed
```

### Example: Resuming After Interruption

```
1. SyncState row exists with resume_token set, status=interrupted
2. Start point: resume_token (not delta_token, not fresh)
3. Call Graph /delta?$skiptoken=<resume_token>
4. Continue from where the previous run left off
5. Repeat until completion or budget exhaustion
```

### Example: Incremental (Delta) Walk

```
1. SyncState row exists with delta_token set, status=completed
2. Start point: delta_token (changes since last complete enumeration)
3. Call Graph /delta?$deltatoken=<delta_token>
4. Graph returns only items that changed since delta_token
5. For each changed item:
   - If status already queued/processing/completed: skip (no re-enqueue)
   - If hash changed: set status=queued, rotate hash, enqueue
   - Else: skip (no work needed)
```

## Step 2: Message Queue — Walker→Processor Decoupling

**Technology:** Azure Queue Storage

**Message structure:** Pydantic `Message` serialized to JSON

```python
class Message(BaseModel):
    drive_item_id: str           # OneDrive item ID
    file_name: str               # e.g., "invoice.pdf"
    folder_path: str             # e.g., "/Matters/Smith-2026-001"
    mime_type: str               # e.g., "application/pdf"
    content_hash: str            # SHA-256 of file bytes
```

**Semantics:**
- **At-least-once delivery:** Queue holds message until processor deletes it; if processor crashes mid-classification, queue re-delivers after visibility timeout
- **Idempotency:** Processor re-checks `content_hash` before downloading; if hash doesn't match, skips and lets walker re-enqueue
- **Poison shedding:** Processor reads Azure's `dequeue_count` (redelivery counter) and marks message as poison if count exceeds threshold

**Design decision:** [ADR-0012](../../spec/adr/0012-cloud-two-job-pipeline.md)

## Step 3: Processor — Queue-Triggered Classification

**Entry point:** `python -m processor` (Azure Container Apps queue-triggered job)

**Input:** One `Message` from the queue

**Scaling:** KEDA's `azure-queue` scaler spawns one replica per queued message; replicas exit after handling their message and scale to zero when queue is empty

### Processor Lifecycle (Per Message)

1. **Dequeue** — Pull one message from queue; parse into `Message` object; read `dequeue_count` (Azure's redelivery counter)
2. **Mark processing** — Update Document row status to `processing`
3. **Hash re-check** — Fetch file from Graph (lightweight metadata call); re-verify `content_hash` matches. Skip (let walker re-enqueue) if hash changed (file was modified while queued)
4. **Download** — Fetch file bytes from SharePoint via Graph ([ADR-0015](../../spec/adr/0015-graph-authenticated-download.md))
5. **Extract text** — Call `extract_text_from_bytes(bytes, mime_type)`:
   - PDF → pypdf page-by-page extraction
   - DOCX → python-docx paragraphs + tables
   - Plain text (JSON, YAML, Markdown, CSV, etc.) → raw text decode with UTF-8-sig → Latin-1 fallback
   - Unsupported type → mark Document `skipped`, delete message, exit
6. **Classify** — Build `SelfConsistencyClassifier` and run N times:
   - Each run: call LLM with category definitions + document text
   - Vote: majority label = category, agreement% = confidence
   - Threshold: if confidence ≤ threshold, resolve to `unknown`
7. **UPSERT result** — Insert or update Document row:
   - Key: `(sync_state_id, drive_item_id)`
   - Set: `category`, `confidence`, `status=completed`
   - Never overwrite: `classification_override` (manual veto)
   - Append: `ProcessingLog` audit entry with token counts and cost
8. **Delete message** — Remove from queue (success path only)
9. **Exit** — Return; KEDA scales down replica

### Error Handling

**Extraction errors (unsupported format, corrupt PDF, etc.):**
- Mark Document `status=skipped`
- Delete message (no retry)
- Exit

**Classification/Graph/Database errors:**
- Mark Document `status=failed`, increment `retry_count`, set `error_message`
- Append ProcessingLog with `status=failed`
- Do NOT delete message; queue re-delivers after visibility timeout
- Re-raise exception for observability (logs, monitoring)
- If `dequeue_count` exceeds threshold: poison message, operator must intervene

**Design decision:** [ADR-0014](../../spec/adr/0014-sharepoint-delta-walker.md)

## Step 4: PostgreSQL — Durable Result Storage

**Three ORM models:**

### SyncState (Walker Position)

```sql
CREATE TABLE sync_state (
  id BIGINT PRIMARY KEY,
  drive_id VARCHAR NOT NULL UNIQUE,  -- SharePoint drive ID
  delta_token VARCHAR,                -- Terminal @odata.deltaLink
  resume_token VARCHAR,               -- Current @odata.nextLink (if interrupted)
  status VARCHAR (idle|walking|interrupted|completed),
  last_synced_at DATETIME,            -- Last *completed* walk timestamp
  updated_at DATETIME                 -- Every write (including interruption)
);
```

**Lifecycle:**
- Insert with `status=idle` on first run
- Set to `status=walking` at walk start
- On budget exhaustion: set `resume_token`, `status=interrupted`, bump `updated_at`
- On completion: set `delta_token`, clear `resume_token`, `status=completed`, stamp `last_synced_at`
- Next run reads this row to determine resumption point

### Document (File State)

```sql
CREATE TABLE documents (
  id BIGINT PRIMARY KEY,
  sync_state_id BIGINT NOT NULL FOREIGN KEY,
  drive_item_id VARCHAR NOT NULL,
  file_name VARCHAR NOT NULL,
  folder_path VARCHAR NOT NULL,
  mime_type VARCHAR,
  content_hash VARCHAR,               -- SHA-256 of current bytes
  previous_hash VARCHAR,              -- SHA-256 before most recent change
  status VARCHAR (queued|processing|completed|skipped|pending|failed),
  category VARCHAR,                   -- Classification result (null if skipped/pending)
  confidence DECIMAL(5, 4),           -- Confidence score (0.0–1.0)
  classification_override VARCHAR,    -- Manual veto (never overwritten)
  retry_count INT,
  error_message VARCHAR,
  last_synced_at DATETIME,
  UNIQUE (sync_state_id, drive_item_id)
);
```

**Status lifecycle:**
- `queued` — Enqueued by walker, awaiting processor
- `processing` — Processor acquired the message and is classifying
- `completed` — Successfully classified; category and confidence set
- `skipped` — Extraction failed (unsupported format) or processing error caused skip
- `pending` — Manual re-classification requested; walker will re-enqueue
- `failed` — Classification attempt failed; awaiting retry or operator intervention

**Invariants:**
- Content hash is compared by processor before download; if mismatched, skip and let walker re-enqueue
- `classification_override` (manual veto) is never overwritten; processor skips classification if override is set
- Multiple concurrent processors can safely UPSERT the same file; last-write-wins for result; audit trail is append-only

### ProcessingLog (Audit Trail)

```sql
CREATE TABLE processing_log (
  id BIGINT PRIMARY KEY,
  document_id BIGINT NOT NULL FOREIGN KEY,
  status VARCHAR (completed|skipped|failed),
  input_tokens INT,
  output_tokens INT,
  total_cost DECIMAL(10, 8),
  created_at DATETIME
);
```

**Purpose:** Record every classification attempt for audit, cost accounting, and debugging. Append-only; processor always adds a new row.

## End-to-End Example

### Scenario: New Invoice Uploaded to SharePoint

```
1. Document (invoice.pdf) added to /Matters/Smith-2026-001 in SharePoint

2. Scheduler triggers Walker (scheduled job)
   a. Read SyncState: delta_token set (incremental walk)
   b. Query Graph /delta?$deltatoken=<delta_token>
   c. Find invoice.pdf in results
   d. Calculate content_hash = SHA-256(bytes)
   e. Insert Document row: drive_item_id, folder_path, mime_type, content_hash, status=queued
   f. Enqueue Message(drive_item_id, "invoice.pdf", "/Matters/Smith-2026-001", "application/pdf", <hash>)
   g. Persist new delta_token (next walk is even more incremental)

3. KEDA detects queued message
   a. Spawn one Processor replica

4. Processor handles the message
   a. Dequeue Message
   b. Update Document.status = processing
   c. Re-fetch file from Graph, verify content_hash matches (ok)
   d. Download file bytes
   e. Extract text via pypdf (multi-page extraction)
   f. Build SelfConsistencyClassifier(categories=["Invoice", "Contract", ...])
   g. Run N=5 times: "Invoice: 5/5, Contract: 0/5, ..." → Verdict(category="Invoice", confidence=1.0)
   h. UPSERT Document: category="Invoice", confidence=1.0, status=completed
   i. INSERT ProcessingLog: status=completed, input_tokens=1234, output_tokens=56, cost=0.00089
   j. DELETE message from queue

5. Result in PostgreSQL
   a. Document row: category="Invoice", confidence=1.0, status=completed
   b. ProcessingLog audit entry for this classification attempt

6. User queries PostgreSQL → sees invoice classified as "Invoice" with 100% confidence
```

### Scenario: File Modified (Hash Changed)

```
1. Previously classified invoice.pdf is replaced with a new version

2. Scheduler triggers Walker
   a. Query Graph /delta
   c. Find invoice.pdf changed (included in delta results)
   d. Calculate new content_hash = SHA-256(new_bytes) — different from old
   e. Find existing Document row by drive_item_id
   f. Set previous_hash = old_hash
   g. Enqueue Message with new content_hash
   h. Update Document: status=queued (re-queueing)

3. Processor classifies the new version
   a. Hash re-check: compare queued message's content_hash with Graph file → match (ok)
   b. Download, extract, classify
   c. Result may differ (new document content)

4. UPSERT updates Document row with new category/confidence
```

### Scenario: Poison Message (Processor Fails Repeatedly)

```
1. Message is queued

2. First processor attempt → Graph error → fail, message re-enqueued (dequeue_count=1)

3. Second processor attempt → Graph error again → fail, message re-enqueued (dequeue_count=2)

4. Third processor attempt → dequeue_count=3 exceeds threshold
   a. Mark Document.status=failed, error_message="Poison message (dequeue_count=3)"
   b. Do NOT delete message (let it eventually expire)
   c. Log alarm for operator

5. Operator investigates:
   a. Check Graph connectivity
   b. Check file still exists in SharePoint
   c. Manually retry or mark Document as skipped
```

## Key Invariants

1. **Resumability:** Walker's time budget and resume token make large enumerations resumable across scheduled runs
2. **Idempotency:** Content hash and status prevent duplicate classifications; processor re-checks before download
3. **Durability:** All state persisted to PostgreSQL; no in-memory job state needed
4. **Scoping:** Root path enforced at Graph delta level, not post-walk filtering
5. **Audit:** ProcessingLog records every classification attempt for cost/compliance
6. **Ordering:** No ordering guarantee between parallel processor replicas; same file classified by one replica at a time (keyed UPSERT)
7. **Manual override:** `classification_override` field allows operator to veto automatic result; processor skips classification if override set

## Related Pages

- [Cloud Seams](../operations/cloud-seams.md) — Message queue, Graph client, auth modes
- [State Store](../operations/state-store.md) — Database schema and ORM models
- [Configuration](../operations/configuration.md) — Environment variables for walker/processor
- [Error Handling](../operations/error-handling.md) — Error types and retry logic
- [Deployment](../operations/deployment.md) — Container build, CI, OIDC, ACA job configuration
