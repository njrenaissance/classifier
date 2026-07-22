# ADR-0013 — Persist state + results in PostgreSQL (via SQLAlchemy)

Status: accepted

## Context

[ADR-0004](0004-csv-file-output.md) writes each run's results to a standalone CSV — right for <100 rows consumed as a flat list, wrong for the cloud pipeline ([ADR-0012](0012-cloud-two-job-pipeline.md)). Processing thousands of files across two decoupled jobs needs **durable, queryable, concurrently-written state**: the walker records what it has seen and its delta position; the classifier records per-file status transitions and results; both must survive job restarts and support resume, retry, and re-classification. A flat CSV per run has no cross-run identity, no status, and no concurrent-writer story.

## Decision

Persist all pipeline state and results in **PostgreSQL** — **Azure Database for PostgreSQL Flexible Server** in production — accessed through **SQLAlchemy**. Tables (detailed in [ADR-0014](0014-sharepoint-delta-walker.md)):

- `sync_state` — walker position per document library (two-token resume, walk status).
- `documents` — one row per file: identity, `content_hash`, processing `status`, and the classification result.
- `processing_log` — per-attempt audit trail plus token/cost accounting.

The classifier **UPSERTs** on `(sync_state_id, drive_item_id)`. The **CSV writer is retained for the local CLI** ([ADR-0004](0004-csv-file-output.md)); the output destination becomes a strategy behind the existing `writer` seam, selected by entry point (CLI → CSV, classifier → DB).

**Note on "serverless".** Azure Database for PostgreSQL Flexible Server has **no auto-pause serverless billing** like Azure SQL Serverless. The low-cost posture is a **Burstable (B-series)** tier that can be stopped/started (up to 7 days) — not per-second pause-on-idle. IaC and cost estimates are written against that reality, not the migration draft's Azure-SQL-Serverless model.

## Alternatives

- **Azure SQL Database (Serverless)** — the migration draft's T-SQL schema, `msodbcsql18`, `pyodbc`; genuine auto-pause billing. Rejected in favour of PostgreSQL per project direction: PostgreSQL keeps the door open for `pgvector` (future RAG) without a second engine and avoids the ODBC driver layer in the image. The two-token / `sync_state` design is engine-neutral.
- **SQLite** — zero-infra, but no concurrent multi-writer story across two independently-scaled jobs; unfit for cloud. Rejected.
- **Keep CSV** — no cross-run state, status, or concurrency. Rejected for production; retained for local dev.
- **Raw `asyncpg`** — lighter, but hand-rolled SQL and no schema/migration ergonomics; SQLAlchemy gives models, migrations (Alembic), and a sync API that fits the sequential per-item classifier. Rejected for now (revisit only if a real async/throughput need appears).

## Tradeoffs

- **Gain:** durable, queryable, concurrently-written state; resume / retry / re-classify fall out of a `status` column; an audit trail with token accounting; one engine for results and future embeddings.
- **Give up:** a managed database to provision, secure (Entra auth / managed identity), and migrate; a real dependency (`sqlalchemy`, a psycopg driver) versus the stdlib `csv` module; more schema to maintain than three CSV columns.

## Consequences

- New modules `src/db.py` (engine/session + SQLAlchemy models) and `src/models.py` (queue-message + row Pydantic models); a `DatabaseWriter` behind the `writer` seam; migrations via Alembic (`schema.sql` acceptable to start).
- Supersedes [ADR-0004](0004-csv-file-output.md) for the **production** path; CSV remains the **local-dev** output.
- Adds a DB failure path to `errors.py` (e.g. `PersistenceError(AppError)`), caught at the job boundary like every other domain failure.
- `documents.classified_by` records the actual model id from settings (`classifier.MODEL`, [ADR-0002](0002-model-haiku-4-5.md)); `classification_override` is never overwritten by the classifier.
- New settings (DB connection URL) join the single `Settings` object per the configuration standard.
