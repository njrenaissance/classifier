# ADR-0017 — Graph content-hash field for change detection

Status: accepted

Supersedes the change-detection hash field of [ADR-0014](0014-sharepoint-delta-walker.md).

## Context

The delta walker ([ADR-0014](0014-sharepoint-delta-walker.md)) detects changed files by comparing Graph's `file.hashes` against the stored `content_hash`. ADR-0014 assumed that field is `sha256Hash`. Microsoft Graph's `hashes` facet, however, does not populate the same field on every drive type: **SharePoint document libraries and OneDrive for Business** expose `quickXorHash` (a Microsoft proprietary hash) and generally do **not** return `sha256Hash`, whereas `sha256Hash`/`crc32Hash` appear mainly on personal OneDrive. ADR-0014 itself flagged "populated `file.hashes`" as an assumption to verify against the real tenant.

The v2 pipeline reads from a SharePoint library ([ADR-0007](0007-sharepoint-app-only-auth.md)), so keying change detection on `sha256Hash` alone would leave `content_hash` empty on a real tenant, silently disabling change detection and re-classifying (or never enqueuing) files.

## Decision

`content_hash` (in `src/graph_client.py`) reads `file.hashes` with a **fallback precedence**: `quickXorHash`, then `sha256Hash`, then `crc32Hash`; it returns `None` when a driveItem has no file facet or no recognised hash. The `quickXorHash`-first order matches what the target SharePoint / OneDrive-for-Business library actually returns, while the fallbacks keep the same code correct on drive types that populate the SHA-256 or CRC32 fields instead.

Change detection remains a stable content hash compared against the stored value (ADR-0014's core decision is unchanged); only the **source field** is refined here. The hash is treated as an opaque change signal — its algorithm is never interpreted, only compared for equality — so mixing hash types across drive types is safe as long as a given item reports the same field over time.

## Alternatives

- **Keep `sha256Hash` only (ADR-0014 as written)** — returns `None` on the SharePoint library, disabling change detection. Rejected.
- **`quickXorHash` only** — correct for the target library but brittle if a drive returns only `sha256Hash`. Rejected in favour of the fallback chain, which costs nothing extra.
- **A configurable single hash field** — one setting naming the field. Rejected: it adds config surface for a value that almost never varies, and the fallback chain already handles every drive type without operator input.

## Tradeoffs

- **Gain:** change detection works against the real SharePoint tenant; the same code is correct across SharePoint, OneDrive for Business, and personal OneDrive without configuration.
- **Give up:** an item that changed which hash field it reports (e.g. a drive-type migration) would look "changed" once and re-classify — a harmless one-off, not a correctness loss.

## Consequences

- `src/graph_client.content_hash` implements the `quickXorHash` → `sha256Hash` → `crc32Hash` precedence; unit tests lock in the ordering and the `None` cases.
- ADR-0014's "compares Graph's `file.hashes` (SHA-256)" clause is superseded by this ADR; a pointer is added at the top of ADR-0014.
- If a future tenant is confirmed to expose only one field, the fallback chain still works unchanged; no code change is required.
