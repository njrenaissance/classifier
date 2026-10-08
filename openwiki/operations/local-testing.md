---
type: Operations Guide
title: Local Testing and Live-Fire Stack
description: Set up and run the docker-compose stack to test the end-to-end two-job pipeline locally against real documents, PostgreSQL, and Azurite without cloud infrastructure
tags: [local-testing, docker-compose, live-fire, filesystem-source, integration-testing, operations]
verified:
  - by: openwiki/0.4.3
    at: 2026-08-28T19:49:26.700Z
sources:
  - id: openwiki-source-2dd95ca607a25b433645d464
    resource: repo://infra/create-queue.py
  - id: openwiki-source-ca0a86464b12e2013094f726
    resource: repo://infra/docker-compose.yml
  - id: openwiki-source-862443b88cee5adeb9e4ba55
    resource: repo://infra/README.md
  - id: openwiki-source-fedad1e58ce8bc5501316b79
    resource: repo://infra/sample-docs/README.md
  - id: openwiki-source-d49e5477b258f0c3fb829ea3
    resource: repo://src/db.py
  - id: openwiki-source-a2690fbc7dfdd4c5929ecd6a
    resource: repo://src/filesystem_walker.py
  - id: openwiki-source-d6651d3bc51203d33893f15c
    resource: repo://src/processor.py
  - id: openwiki-source-28c21d7c5df204edb1750fd6
    resource: repo://tests/test_filesystem_pipeline_integration.py
generated: { by: "openwiki/0.4.3", at: "2026-08-28T19:49:26.700Z" }
---

# Local Testing and Live-Fire Stack

This guide walks you through **two paths** to test the two-job pipeline locally without SharePoint/Graph infrastructure:

1. **Integration tests** (Pytest, **no cost**) — exercises real PostgreSQL + real Azurite with a faked voter; run via `pytest -m integration`
2. **Live-fire stack** (docker-compose, **real LLM calls**) — exercises the full end-to-end path with real classification; runs locally and costs money per invocation

Choose integration tests for rapid, cost-free validation. Use the live-fire stack when you need to verify real classification behavior against your own documents.

## Key Differences: Integration Tests vs. Live-Fire Stack

| Aspect | Integration Tests | Live-Fire Stack |
|--------|---|---|
| **Entry point** | `pytest -m integration tests/test_filesystem_pipeline_integration.py` | `docker compose -f infra/docker-compose.yml up` |
| **Infrastructure** | testcontainers (Docker-managed Postgres + Azurite) | docker-compose (manually managed) |
| **Classification** | Faked voter (deterministic, no LLM) | Real self-consistency voter (calls Anthropic API) |
| **Cost** | Free | Real API charges |
| **Speed** | ~30 seconds | Depends on document count and API latency |
| **Suitable for** | Pre-merge validation, CI gates, fast feedback loops | Live testing against your own documents, acceptance testing |

## Integration Tests: No-Cost Validation

The integration test exercises the **real files → real queue → real PostgreSQL** leg of the pipeline with a **faked voter** so no LLM calls are made:

```bash
# Requires Docker; skips if unavailable
uv run pytest -m integration tests/test_filesystem_pipeline_integration.py
```

**What happens:**
1. Testcontainers spins up a real PostgreSQL 16 container and runs `alembic upgrade head` (the real migration)
2. Testcontainers spins up a real Azurite queue container
3. `FilesystemWalker` enumerates a temp directory of valid PDFs, hashes them, UPSERTs document rows, and enqueues work items
4. `Processor` consumes each message, reads the bytes from disk, extracts text, and UPSERTs a `completed` result
5. Assertions verify the documents table shows correct status and results

The voter is a `_FakeVoter` that returns a deterministic verdict (`category='contract'`, `confidence=0.9`) without calling Anthropic.

**Run this in:**
- Pre-merge CI (already marked `pytest.mark.integration`)
- Local development before pushing (fast, no cost, confirms wiring)
- Any time you want to validate the Postgres + Azurite + message flow without real LLM calls

## Live-Fire Stack: Full End-to-End Testing

The **live-fire stack** is a manual docker-compose setup that runs the entire two-job pipeline against a mounted host directory with real classification:

```
real files (your documents) 
  ↓
FilesystemWalker (full re-enumeration)
  ↓
PostgreSQL (real state store)
  ↓
Azure Queue Storage / Azurite (real message queue)
  ↓
Processor (queue-triggered, classifies ONE document per invocation)
  ↓
Anthropic API (real self-consistency voter)
```

> [!WARNING]
> The processor makes **real LLM calls** to Anthropic / Microsoft Foundry. This stack **costs money per run**. It is deliberately **not** part of CI. For an automated, no-cost wiring check, use the integration test instead.

## Prerequisites for the Live-Fire Stack

1. **Docker and docker-compose** installed and running
2. **ANTHROPIC_API_KEY** environment variable set with a valid Anthropic API key
3. **Sample documents** — `.pdf` and/or `.docx` files to classify
4. **Image built** — the stack expects `classifier:ci` Docker image

### Build the image

```bash
docker build -t classifier:ci .
```

### Set up your API key (PowerShell on Windows)

```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-your-key-here"
```

Or on macOS/Linux:

```bash
export ANTHROPIC_API_KEY="sk-ant-your-key-here"
```

### Choose your documents

**Option A: Use the default sample-docs directory**

Drop `.pdf` or `.docx` files into `infra/sample-docs/`:

```bash
cp your-documents/*.pdf infra/sample-docs/
cp your-documents/*.docx infra/sample-docs/
```

**Option B: Point at your own folder (skip the bind-mount)**

```powershell
# Windows PowerShell
$env:SAMPLE_DOCS = "C:\Users\your-name\my-test-documents"
docker compose -f infra/docker-compose.yml up --abort-on-container-exit
```

```bash
# macOS/Linux
export SAMPLE_DOCS="/path/to/your/documents"
docker compose -f infra/docker-compose.yml up --abort-on-container-exit
```

## Running the Live-Fire Stack

### Start the stack

```bash
docker compose -f infra/docker-compose.yml up --abort-on-container-exit
```

`--abort-on-container-exit` stops the stack when any service exits (the processor processes one document and exits by design).

### Service startup order and health checks

The stack defines a dependency graph so services start in the right order:

1. **postgres** — Health-checked with `pg_isready`; services wait for `service_healthy`
2. **azurite** — Queue Storage emulator; services wait for `service_started`
3. **migrate** — One-shot `alembic upgrade head` (schema migration); depends on postgres healthy
4. **queue-init** — One-shot idempotent queue creation; depends on azurite started
5. **walker** — Full re-enumeration of `/data`; depends on postgres + azurite + migrate + queue-init complete
6. **processor** — Classifies ONE queued document; depends on postgres + azurite + migrate + queue-init complete

**Timeline:**
```
postgres becomes healthy
  ↓
migrate applies schema
  ↓
queue-init creates the queue
  ↓
walker enumerates /data, hashes files, enqueues changed ones
  ↓
processor receives one message, classifies it with a real verdict, exits
```

Once `walker` and `processor` both exit, the whole `docker compose up` completes.

## Draining the Entire Queue

The processor is **single-shot by design** (`run_once`): it handles exactly one message and exits. KEDA (in production) spawns one replica per queued message, so there is no polling loop baked into the code.

To classify every queued document locally, re-run the processor until the queue drains:

```powershell
# After docker compose up has populated the queue
while ($true) { docker compose -f infra/docker-compose.yml run --rm processor }
```

```bash
# macOS/Linux
while true; do docker compose -f infra/docker-compose.yml run --rm processor; done
```

Each `run` processes one message and exits with `0`. When the queue is empty, the log shows:

```
Queue empty; nothing to process
```

The loop then terminates (or catches the exit and continues — adjust the loop condition as needed).

## Inspecting Results in PostgreSQL

After the pipeline finishes, query the results directly:

```bash
docker compose -f infra/docker-compose.yml exec postgres \
  psql -U classifier -d classifier -c \
  "select drive_item_id, status, category, confidence from documents order by drive_item_id;"
```

**Output example:**
```
drive_item_id  | status    | category  | confidence
-------------------------------------------------------
contract.pdf   | completed | contract  | 0.9
invoice.pdf    | completed | invoice   | 0.85
(2 rows)
```

### Audit trail: per-attempt details

The `processing_log` table records every classification attempt — category, confidence, token counts (when available), and cost:

```bash
docker compose -f infra/docker-compose.yml exec postgres \
  psql -U classifier -d classifier -c \
  "select document_id, attempt, status, category, confidence from processing_log order by document_id, attempt;"
```

### Sync state: walker position

The `sync_state` table tracks walker progress (for SharePoint delta walks) and the filesystem source (no delta tokens):

```bash
docker compose -f infra/docker-compose.yml exec postgres \
  psql -U classifier -d classifier -c \
  "select drive_id, walk_status, last_synced_at from sync_state;"
```

## Docker Compose Stack Components

Each service in `infra/docker-compose.yml` plays a specific role:

### postgres (State Store)

```yaml
postgres:
  image: postgres:16-alpine
  healthcheck: pg_isready -U classifier -d classifier
```

- **Role:** Persistent state store for documents, sync state, and processing logs
- **Health check:** Listens on port 5432; services depend on `service_healthy`
- **Lifecycle:** Runs until stopped; backed by a Docker volume so data persists across restarts (until `docker compose down -v`)

### azurite (Message Queue)

```yaml
azurite:
  image: mcr.microsoft.com/azure-storage/azurite:latest
  command: azurite-queue --queueHost 0.0.0.0 --queuePort 10001
```

- **Role:** Azure Queue Storage emulator
- **Endpoint:** `http://azurite:10001/devstoreaccount1`
- **Credentials:** Well-known dev-storage account (not a secret; see `docker-compose.yml`)
- **Lifecycle:** Runs until stopped; backed by a Docker volume so queued messages persist (until `docker compose down -v`)

### migrate (Schema Migration)

```yaml
migrate:
  image: classifier:ci
  command: alembic upgrade head
  restart: "no"
```

- **Role:** One-time schema migration (fix-forward, never self-migrating)
- **Dependencies:** Waits for `postgres` to be healthy
- **Exit:** Exits with `0` on success; the walker/processor services depend on `service_completed_successfully`
- **Idempotency:** `alembic upgrade` is idempotent — re-running does nothing if the schema is up-to-date

### queue-init (Queue Creation)

```yaml
queue-init:
  image: classifier:ci
  command: python /app/create-queue.py
  restart: "no"
```

- **Role:** Idempotent queue creation (neither Azurite nor the app auto-creates it)
- **Script:** `infra/create-queue.py` (bind-mounted into the container)
- **Behavior:**
  - Reads `CLASSIFIER__QUEUE_NAME` and `CLASSIFIER__QUEUE_CONNECTION_STRING` from the environment
  - Calls `QueueClient.create_queue()`
  - Treats an already-existing queue as success (idempotent)
  - Retries up to 30 times with 2-second delays if Azurite is still starting up
- **Dependencies:** Waits for `azurite` to be started
- **Exit:** Exits with `0` when the queue is created or already exists

### walker (Producer / Enumerator)

```yaml
walker:
  image: classifier:ci
  command: python -m walker
  environment:
    CLASSIFIER_SOURCE: filesystem
    CLASSIFIER__FILESYSTEM_ROOT: /data
    ...
  volumes:
    - ${SAMPLE_DOCS:-./sample-docs}:/data:ro
```

- **Role:** Full re-enumeration of `/data`; enqueues new/changed files
- **Source:** Filesystem (ADR-0020) — no Graph, no SharePoint
- **Input:** All supported files (`.pdf`, `.docx`, etc.) in the mounted `SAMPLE_DOCS` directory
- **Output:**
  - Inserts `documents` rows with status `queued` and content hashes
  - Enqueues `Message` items on the queue (one per file)
  - Inserts/updates a synthetic `sync_state` row (keyed `filesystem:<root>`)
- **Dependencies:** Waits for postgres + azurite + migrate + queue-init all complete
- **Lifecycle:** Runs once to completion; enumerates the tree, enqueues changed files, then exits

### processor (Consumer / Classifier)

```yaml
processor:
  image: classifier:ci
  command: python -m processor
  environment:
    <<: *filesystem_env
    ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY:?set ANTHROPIC_API_KEY...}
  volumes:
    - ${SAMPLE_DOCS:-./sample-docs}:/data:ro
```

- **Role:** Consume ONE work item from the queue; classify it with a real self-consistency voter
- **Source:** Filesystem (ADR-0020) — reads document bytes from `/data`
- **Per-invocation behavior:**
  1. Dequeue one message (or exit if queue is empty)
  2. Hash re-check via the filesystem source
  3. Download file bytes from disk
  4. Extract text by MIME type
  5. Classify with self-consistency voter (calls Anthropic API **real money**)
  6. UPSERT result to `documents` table
  7. Append `processing_log` audit row
  8. Delete the message from the queue
  9. Exit `0`
- **Failure handling:** If any step fails, mark `documents.status = failed`, append a `processing_log` entry, re-raise the exception, and exit `1` (message stays on queue for redelivery)
- **Dependencies:** Waits for postgres + azurite + migrate + queue-init all complete
- **Idempotency:** Respects manual `classification_override` (human labels are never overwritten)

## Shared Environment Configuration

Both walker and processor share these settings (defined via `x-filesystem-env` anchor):

```yaml
CLASSIFIER_SOURCE: filesystem              # Use mounted dir, not SharePoint
CLASSIFIER__FILESYSTEM_ROOT: /data         # Mount location in container
CLASSIFIER__DATABASE_URL: ...              # PostgreSQL connection
CLASSIFIER__QUEUE_NAME: classifier-work-items
CLASSIFIER__QUEUE_CONNECTION_STRING: ...   # Azurite connection (dev-storage account)
```

The processor adds:

```yaml
ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY:?...}  # Required; must be set in host env
```

## Troubleshooting

### "Connection refused" when postgres starts

The postgres container is taking longer than the default health-check timeout (60 seconds total, 3s interval × 20 retries). Check `docker logs <container>` to see if the database is actually starting, or increase the retries in `docker-compose.yml`.

### Queue creation fails ("Azurite not ready yet")

Azurite takes a few seconds to bind the port. The `queue-init` service retries up to 30 times; if it still fails, check:

```bash
docker logs <container-name>-azurite-1
```

### Documents table is empty after walker exits

1. Check that `SAMPLE_DOCS` points to a directory with `.pdf`/`.docx` files
2. Check walker logs: `docker logs <container-name>-walker-1`
3. Verify the filesystem source only enumerated supported suffixes (see `src/extraction.py`)

### Processor says "Queue empty; nothing to process"

Either:
- Walker ran but enqueued nothing (all files were unchanged; run `docker compose down -v` to wipe the database and try again)
- The queue connector is misconfigured (check `CLASSIFIER__QUEUE_CONNECTION_STRING` in `docker-compose.yml`)

### "ANTHROPIC_API_KEY not set" when processor starts

Set the key before running docker compose:

```powershell
# Windows PowerShell
$env:ANTHROPIC_API_KEY = "sk-ant-..."
docker compose -f infra/docker-compose.yml up
```

### Wipe everything and start fresh

Clear Docker volumes (deletes Postgres data and Azurite queue state):

```bash
docker compose -f infra/docker-compose.yml down -v
```

Then rebuild and re-run:

```bash
docker build -t classifier:ci .
docker compose -f infra/docker-compose.yml up --abort-on-container-exit
```

### Inspect running containers while the stack is up

In another terminal:

```bash
# See all services
docker compose -f infra/docker-compose.yml ps

# View logs for one service
docker compose -f infra/docker-compose.yml logs -f walker

# Execute a shell command in a running container
docker compose -f infra/docker-compose.yml exec postgres bash
```

## Filesystem Source Overview

The filesystem source (ADR-0020) is a **local alternative to SharePoint** for testing and development:

- **Walker:** `FilesystemWalker` (in `src/filesystem_walker.py`) enumerates a mounted directory, hashes each supported file, and enqueues the new/changed ones
- **Processor:** `FilesystemContentSource` (in `src/content_source.py`) reads file bytes from disk instead of calling Microsoft Graph

**Key differences from SharePoint:**

| Aspect | SharePoint | Filesystem |
|--------|---|---|
| Source enumeration | Delta walk via Graph (resumable, incremental) | Full re-enumeration (no delta token) |
| Idempotency | Tracked by content hash + status | Tracked by content hash + status |
| Sync state | Two-token position (delta + resume) | Synthetic position (keyed `filesystem:<root>`) |
| Identifiers | OneDrive item IDs (opaque) | File path relative to root (stable, human-readable) |

## Configuration Reference

### Environment Variables for the Live-Fire Stack

These are set automatically by the docker-compose file (see `x-filesystem-env`):

- `CLASSIFIER_SOURCE=filesystem` — Use mounted directory source
- `CLASSIFIER__FILESYSTEM_ROOT=/data` — Root path in container
- `CLASSIFIER__DATABASE_URL=postgresql+psycopg://classifier:classifier@postgres:5432/classifier` — Postgres connection
- `CLASSIFIER__QUEUE_NAME=classifier-work-items` — Queue name
- `CLASSIFIER__QUEUE_CONNECTION_STRING=...` — Azurite dev-storage account connection

You override `SAMPLE_DOCS` to point at a different host directory:

```bash
export SAMPLE_DOCS="/path/to/my/documents"
docker compose -f infra/docker-compose.yml up
```

### Categories definition

The processor requires a `categories.md` file that defines the classification categories (e.g., contract, invoice, lease). This is passed via:

```bash
CLASSIFIER__PROCESSOR_CATEGORY_FILE=categories.md
```

The docker-compose file does not override this, so it uses the default path in the image. If your categories change, rebuild the image or pass a bind-mounted file.

## Next Steps

- **For rapid validation:** Run the integration test (`pytest -m integration`)
- **For acceptance testing:** Use the live-fire stack with your own documents
- **For production:** Deploy to Azure Container Apps; the walker runs on a schedule and the processor scales via KEDA (see `/openwiki/operations/deployment.md`)
- **For debugging a specific document:** Extract it to a temp directory, point `SAMPLE_DOCS` at it, and run the live-fire stack with verbose logging

## Related Reading

- [Configuration Management](/openwiki/operations/configuration.md) — Environment variables and settings singleton
- [Testing Strategy](/openwiki/testing.md) — Unit test patterns and fixtures
- [Cloud Pipeline Workflow](/openwiki/workflows/cloud-pipeline.md) — Two-job architecture and state management
- [Cloud Boundaries](/openwiki/operations/cloud-boundaries.md) — Walker/processor interaction patterns
- [State Store](/openwiki/operations/state-store.md) — PostgreSQL schema and persistence
