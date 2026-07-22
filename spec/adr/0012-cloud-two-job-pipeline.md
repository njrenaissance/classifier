# ADR-0012 — Two-job cloud pipeline (walker + classifier) on Azure Container Apps

Status: accepted

## Context

The original design ([ADR-0003](0003-cli-batch-interface.md)) is an on-demand CLI batch job over <100 local files that writes a CSV. The production target has changed: classify documents from a SharePoint document library **at scale** (thousands of files, growing), **incrementally** (only changed/new files after the first pass), on a recurring schedule, with durable per-file state so runs are resumable and re-triggerable.

A single long-running batch process no longer fits. Enumeration (walking SharePoint) and per-document work (download → extract → classify) have different runtimes, failure modes, scaling needs, and retry semantics: enumeration is one bounded I/O-bound walk; per-document work is embarrassingly parallel and API-bound. The classification core (extraction → self-consistency → verdict) is unchanged and already source-agnostic.

## Decision

Split production into **two Azure Container Apps (ACA) Jobs**, shipped from **one image** with two entry points, decoupled by an **Azure Queue Storage** queue:

- **Walker** (`python -m walker`) — ACA **scheduled** job. Runs a resumable, time-budgeted Microsoft Graph delta walk of the library, tracks its position in PostgreSQL, and **enqueues one work item per changed/new file** ([ADR-0014](0014-sharepoint-delta-walker.md)).
- **Classifier** (`python -m processor`) — ACA **queue-triggered** job (KEDA `azure-queue` scaler). For one work item: download the file via Graph ([ADR-0015](0015-graph-authenticated-download.md)), extract text, classify with self-consistency ([ADR-0005](0005-confidence-self-consistency.md)), and UPSERT the result to PostgreSQL ([ADR-0013](0013-postgresql-state-store.md)).

The **queue and async scaling are Azure-native**: the walker *produces* messages and KEDA *consumes* them by spawning classifier replicas — the codebase contains no queue-polling loop or worker pool. The **existing CLI is retained for local development** (local files → CSV), sharing the same classification core.

## Alternatives

- **Keep one batch process** (enumerate + process in a single run) — simplest, but couples enumeration to processing, has no natural horizontal scale for the per-document work, and re-does the whole walk on every failure. Rejected for the new scale/incremental requirements.
- **A single always-on worker service** polling a queue — viable, but pays for idle compute between runs and adds a service lifecycle; ACA queue-triggered jobs scale to zero. Rejected as more to operate for a bursty, batch workload.
- **Azure Functions instead of ACA Jobs** — good queue triggers, but the image needs system libraries (extraction deps, future `.doc` tooling) and a plain container is a cleaner fit than the Functions runtime; ACA Jobs keep the "one image, two commands" story.
- **DB-as-queue** (classifier polls a `pending` table) — removes the queue service, but re-implements visibility timeouts, dequeue-count retries, and poison handling that Azure Queue Storage gives for free. Rejected.

## Tradeoffs

- **Gain:** enumeration and per-document work scale and fail independently; per-file durable state enables resume, retry, and re-classification; the classifier scales to zero when the queue is empty; one image keeps build/deploy simple.
- **Give up:** more moving parts than a CLI (a queue, a scheduler, two job definitions, managed identity); local end-to-end no longer exercises the full production path (the CLI covers the core, not the walker/queue seam).

## Consequences

- New entry points `src/walker.py` and `src/processor.py`; the classification core (`categories`, `extraction`, `classifier`, `self_consistency`) is reused unchanged.
- Revises `spec/spec.md` **Scale**: production is queue-driven horizontal scaling; the local CLI stays sequential and single-process.
- Extends [ADR-0003](0003-cli-batch-interface.md): the CLI remains the local-dev interface but is no longer the production interface.
- Requires Azure infrastructure (ACA environment + two jobs, Queue Storage, registry, Key Vault, managed identity, Log Analytics) captured as IaC, and a Dockerfile, in follow-up issues.
- Per-file state and the work-item (queue message) contract live in PostgreSQL — [ADR-0013](0013-postgresql-state-store.md); the walker's enqueue/skip logic — [ADR-0014](0014-sharepoint-delta-walker.md).
