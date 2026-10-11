# ADR-0023 — Azure Blob Storage source (third `CLASSIFIER_SOURCE` mode)

Status: accepted

## Context

ADR-0020 added a source-mode toggle (`CLASSIFIER_SOURCE`) with `sharepoint` and
`filesystem`. Issue #70 adds a third mode, `blob`, that enumerates and classifies
documents held in an Azure Blob Storage container.

The motivating case is the sibling `evidence` project, which makes Blob Storage the
authoritative repository for legal evidence and copies only *eligible* files into the
SharePoint Matters Hub. Reading SharePoint would bound classification coverage by a
capacity rule in that copy step rather than by the evidence that exists, and would
classify working copies rather than originals. Reading the container directly removes
that dependency and keeps the classifier's Graph credentials read-only.

Three choices are load-bearing for whoever extends this later, so they are recorded here:
how a blob's content hash is obtained, how the container is enumerated, and how the
classifier authenticates.

## Decision

Add `blob` as a third `CLASSIFIER_SOURCE` value, implemented as a third producer and a
third retrieval seam over the existing ADR-0020 contracts (no change to `Enqueuer`, the
queue, or the processor's classification path):

- **Identity.** `Document.drive_item_id` is the blob name relative to the configured
  prefix. `SyncState.drive_id` is the synthetic `blob:<account_url>/<container>/<prefix>`.
  `MessageSource.blob` discriminates the message. Both `delta_token` and `resume_token`
  stay `NULL`.
- **Content hash.** The `sha256` of the blob is read from a blob **metadata** property
  named `sha256`, written at upload by the evidence workflow. This avoids downloading
  every blob per run. A blob without the property is downloaded and hashed with
  `hash_bytes`, so enqueue-time and processor-time hashes always agree. Blob
  `Content-MD5` and ETag/`Last-Modified` were rejected (see Options).
- **Enumeration.** Full `list_blobs` re-enumeration on every run, the same shape as
  `FilesystemWalker`. The Blob change feed was rejected for now.
- **Authentication.** Managed identity via `DefaultAzureCredential`, with the
  least-privilege role `Storage Blob Data Reader`. No account key or SAS token is read.
- **Relationship to SharePoint.** Additive. The SharePoint path is unchanged and is
  selected per deployment. Retiring it is a separate decision.

Configuration lives in a `BlobSettings` section (`CLASSIFIER__BLOB_ACCOUNT_URL`,
`CLASSIFIER__BLOB_CONTAINER`, optional `CLASSIFIER__BLOB_PREFIX`), and the `walker.run` /
`processor.run` dispatch gains an explicit `blob` branch that constructs no `GraphClient`.

### Options considered

- **Hash from `Content-MD5`.** Rejected. It is populated only when set at upload, and it
  is MD5, not the SHA-256 the evidence workflow records.
- **Hash from ETag / `Last-Modified`.** Rejected. It changes on metadata-only writes and
  does not match the content-hash semantics of ADR-0017.
- **Blob change feed for delta.** Deferred. It would restore delta semantics but requires
  a second walker shape and a resume token; full enumeration matches ADR-0020 and is simpler.
- **SAS token or account key.** Rejected. Both are long-lived secrets to rotate and store;
  managed identity needs neither, and the Graph path already uses managed identity for
  production.

## Consequences

- **Easier:** classification coverage follows the authoritative store, not a copy step.
  The producer and retrieval seam are small, mirror existing code, and reuse the shared
  enqueue decision and processor.
- **Harder:** every run lists the whole prefix, so cost grows with container size. A
  metadata-less blob costs one download to hash, once per content change.
- **Deployment constraint:** the container must sit in an online tier (Hot, Cool, or Cold).
  Archive-tier blobs cannot be downloaded, so their processing fails until rehydration.
  Enqueue-time metadata reads work in every tier, so change detection is unaffected.
- **Trust constraint:** the `sha256` metadata is written by the upstream workflow and is
  trusted as the content hash. If it is wrong or absent, the enqueue decision inherits
  the error. Verifying it would mean downloading every blob, which this decision avoids.
- **Forecloses (for now):** a blob change-feed delta walker, and SAS/key-based access,
  without a superseding ADR.
- **Follow-ups:** an Azurite-backed integration test (which would need a
  `docker-compose.yml` under the CI rule in `testing.md`) and an explicit decision on
  whether this supersedes the SharePoint walker for production.
