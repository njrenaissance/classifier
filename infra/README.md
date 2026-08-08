# Live-fire stack — filesystem source (ADR-0020, #61)

A **manual, local** docker-compose stack that runs the full two-job pipeline against
a **mounted host directory** instead of SharePoint/Graph, so you can exercise the
real path end to end:

> real files → real queue (Azurite) → real PostgreSQL → **real** classification

> [!WARNING]
> The processor makes **real LLM calls** (Anthropic / Foundry). This stack needs a
> valid `ANTHROPIC_API_KEY` and **costs money per run**. It is deliberately **not**
> part of CI. For an automated, no-cost check of the same wiring (real Postgres +
> real Azurite, faked voter), run the integration test instead:
> `uv run pytest -m integration tests/test_filesystem_pipeline_integration.py`.

## What's in the stack

| Service | Role |
|---|---|
| `postgres` | State store (`postgres:16-alpine`), health-checked. |
| `azurite` | Local Azure Queue emulator. |
| `migrate` | One-shot `alembic upgrade head`. Jobs **never self-migrate** (fix-forward, ADR-0013); the walker/processor `depends_on` it completing. |
| `queue-init` | One-shot idempotent creation of the work queue (neither Azurite nor the app auto-creates it). |
| `walker` | `python -m walker` with `CLASSIFIER_SOURCE=filesystem`: full re-enumeration of `/data`, enqueues changed files. |
| `processor` | `python -m processor`: classifies **one** queued document per invocation, then exits. |

## Prerequisites

1. Build the image (the stack uses `classifier:ci`):
   ```bash
   docker build -t classifier:ci .
   ```
2. Export your API key (PowerShell):
   ```powershell
   $env:ANTHROPIC_API_KEY = "sk-ant-..."
   ```
3. Choose the documents to classify — either drop `.pdf`/`.docx` files into
   [`sample-docs/`](sample-docs/), or point at your own folder:
   ```powershell
   $env:SAMPLE_DOCS = "C:\Users\jon_m\testdocs"
   ```

## Run it

```powershell
docker compose -f infra/docker-compose.yml up --abort-on-container-exit
```

Order of events: `postgres` becomes healthy → `migrate` applies the schema →
`queue-init` creates the queue → `walker` populates PostgreSQL and the queue from
`/data` → `processor` classifies **one** document with a real verdict.

### Draining the whole queue

The processor is **single-shot** by design (`run_once`, one message per invocation —
KEDA spawns one replica per message in production, ADR-0012). There is intentionally
**no** polling loop baked into the entrypoint. To classify every queued document
locally, re-run the processor until the queue drains:

```powershell
# after `up` has populated the queue
while ($true) { docker compose -f infra/docker-compose.yml run --rm processor }
```

Each invocation processes one message and exits `0`; once the queue is empty the run
logs `Queue empty; nothing to process`.

## Inspect the results

```bash
docker compose -f infra/docker-compose.yml exec postgres \
  psql -U classifier -d classifier -c \
  "select drive_item_id, status, category, confidence from documents order by drive_item_id;"
```

`processing_log` holds the per-attempt audit trail (status, category, tokens, cost).

## Tear down

```bash
docker compose -f infra/docker-compose.yml down -v
```

`-v` also removes the Postgres/Azurite volumes for a clean next run.
