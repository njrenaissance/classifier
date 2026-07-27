# ADR-0019 — Config-driven walk scope, enforced at the Graph delta level

Status: accepted

Supersedes the non-`Matters` skip rule and `skipped`-tracking of out-of-scope files of [ADR-0014](0014-sharepoint-delta-walker.md).

## Context

ADR-0014 walked the **entire** drive (`/drives/{id}/root/delta`) and then applied a **hard-coded** `/Matters/` filter in the walker: files under `/Matters/` were enqueued, everything else was UPSERTed `status='skipped'` and never classified. That ADR itself flagged the arrangement as provisional — its final consequence records that "the `_root` / non-`Matters` skip rule … are library-layout assumptions; a different library layout is a config/ADR change, not code sprinkled with special cases."

Two forces make it worth acting on now. First, live integration testing wants to point the walker at a **small subtree** without crawling a whole library (issue #59). Second, the hard-coded prefix is exactly the special-case ADR-0014 anticipated generalizing. The cloud pipeline is not yet deployed (the Dockerfile and IaC work is still open), so there is no live `sync_state`/delta history to migrate.

## Decision

Make the walk root a single piece of configuration — `WalkerSettings.root_path` (env `CLASSIFIER__WALKER_ROOT_PATH`, default `/Matters`) — and enforce it at the **Graph delta level** rather than by a post-walk filter. A fresh walk starts from `/drives/{id}/root:/{root_path}:/delta` (Graph path addressing, each segment percent-encoded); an empty `root_path` or a bare `/` walks the whole drive via `/root/delta`. Because Graph then returns only in-scope driveItems, the walker's `/Matters/` filter and its `skipped`-tracking of out-of-scope files are **removed**: every file the walk yields is in scope and is subject only to the existing new/changed/in-flight/pending enqueue rules.

Scope lives only in the *initial* delta URL. A saved `resume_token`/`delta_token` already encodes the scope of the walk that produced it, so it continues to take precedence over `root_path` and a scoped resume replays the correct page.

## Alternatives

- **Keep the hard-coded `/Matters` post-walk filter, add an independent subtree knob on top** — leaves two overlapping scoping mechanisms and still crawls the whole drive every run; the subtree knob would then have to sit under `/Matters` to have any effect. Rejected as the muddier design.
- **Address the subtree by folder item id (`/items/{id}/delta`)** — the canonical folder-scoped delta form, but the id is opaque and must be looked up first; a human-typed folder path is far friendlier for the integration-testing use case. Kept as a documented fallback if a tenant rejects path-addressed delta.
- **Filter by `root_path` in the walker after a full-drive delta** — preserves `skipped` rows for out-of-scope files, but still enumerates the whole drive, defeating issue #59's "don't walk the whole drive." Rejected.

## Tradeoffs

- **Gain:** one config-driven scoping knob replaces a hard-coded layout assumption; a scoped walk enumerates only the subtree (cheaper, and the point of the feature); the walker's per-item logic drops a branch.
- **Give up:** out-of-scope files are no longer recorded as `skipped` rows — they are simply never seen. That bookkeeping was only ever a side effect of crawling the whole drive; nothing in the pipeline consumed it. Correctness now depends on Graph accepting path-addressed delta (`/root:/{path}:/delta`); the item-id form is the fallback if it does not.

## Consequences

- `src/config.py` adds `WalkerSettings.root_path` (default `/Matters` via `DEFAULTS`); `.env.example` documents `CLASSIFIER__WALKER_ROOT_PATH`.
- `src/graph_client.py` builds the initial delta URL from `root_path` (new `iter_delta_pages(..., root_path=...)` keyword); `start_url` still wins.
- `src/walker.py` threads `root_path` through `WalkRequest`/`Walker`/`run` and removes `_is_in_matters`, `_mark_skipped`, and the `/Matters` constants. `DocumentStatus.skipped` stays in the enum — the **processor** still uses it (an unsupported/empty document); only the walker stops producing it.
- ADR-0014's non-`Matters` skip rule is superseded; a pointer is added at the top of ADR-0014. `spec/spec.md`'s ingestion note is updated to describe the configurable subtree scope.
