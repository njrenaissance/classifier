# ADR-0021 — Plain-text format extraction

Status: accepted

## Context

[[0006-text-extraction-per-format-libs]] chose per-format Python libraries for
text extraction and shipped PDF (`pypdf`) and DOCX (`python-docx`);
[[0009-defer-legacy-doc-extraction]] pinned legacy `.doc` as deferred. Everything
else — including files that are *already* plain text — was treated as an
unregistered suffix/MIME and rejected with `UnsupportedFormatError`, and
`LocalFileSystemSource` filtered those files out of enumeration entirely.

In practice many documents worth classifying arrive as plain text: notes, config,
exports, logs, JSON/YAML data. These formats need **no parsing library at all** —
just a byte→text decode — so rejecting them costs coverage on both the local CLI
and the v2 cloud pipeline (ADR-0015) for no real benefit. Issue #63 adds them.

This ADR **extends** ADR-0006 (per-format strategies in one registry); it does not
revisit the per-format-libraries decision, which stands.

## Decision

Add a **`PlainTextExtractor`** strategy to the `src/extraction.py` registry and
register the plain-text formats:

- **Suffixes:** `.txt`, `.json`, `.yml`, `.yaml`, `.md`, `.csv`, `.xml`.
- **MIME types:** `text/plain`, `application/json`, `application/yaml`
  (+ aliases `application/x-yaml`, `text/yaml`), `text/markdown`, `text/csv`,
  `application/xml` (+ alias `text/xml`), **plus a `text/*` catch-all** so any text
  subtype the Graph API returns for a SharePoint item is handled.

Sub-decisions:

- **Raw decode, not structured parsing.** JSON/YAML/XML are decoded as raw text —
  the classifier wants the textual content, and a raw decode keeps a single
  strategy serving every text format, format-agnostic.
- **Encoding policy: `utf-8-sig` → `latin-1` fallback.** Decode as `utf-8-sig`
  (transparently strips a BOM and handles plain UTF-8); on `UnicodeDecodeError`
  fall back to `latin-1`, which maps every byte and never raises. Extraction
  therefore always yields text rather than rejecting an oddly-encoded file.
- **`text/*` catch-all over a closed allow-list.** In `extract_text_from_bytes`,
  a `text/*` MIME with no explicit entry falls back to the plain-text strategy.

`src/sources.py` needs no change: it defines "supported" solely by
`extraction.supported_suffixes()`, so the new suffixes enumerate automatically.

## Alternatives

- **Reject non-UTF-8 with `ExtractionError`** (strict decode). Surfaces bad
  encodings loudly, but rejects real-world text files with legacy encodings; for a
  classifier, lossy-but-present text beats a hard failure. Rejected.
- **`errors="replace"`** instead of a `latin-1` fallback. Never fails either, but
  scatters U+FFFD through the text; `latin-1` produces a cleaner lossy mapping.
  Rejected.
- **Structurally parse JSON/YAML** (extract values, flatten keys). More work, a
  parser dependency per format, and no classifier benefit over raw text. Rejected.
- **Closed MIME allow-list, no catch-all.** Simpler (a pure dict lookup), but
  rejects text subtypes we didn't foresee from SharePoint. Rejected in favour of
  the catch-all's robustness.

## Tradeoffs

- **Gain:** broad plain-text coverage on both entry points with no new dependency;
  robust decoding that never rejects a text file; SharePoint text MIMEs handled
  whatever the subtype.
- **Give up:** the `latin-1` fallback can silently produce mojibake on truly
  non-UTF-8, non-latin-1 bytes; raw decode means no structural signal from
  JSON/YAML. Both are acceptable for classification.

## Consequences

- `PlainTextExtractor` requires the suffix↔MIME maps to move from a strict 1:1 to
  many-MIME→one-suffix (aliases; `.yml`/`.yaml` share a canonical MIME). The
  registry is restructured so `_SUFFIX_TO_MIME` is the canonical source of truth
  and `_MIME_TO_SUFFIX` is derived from it plus alias entries.
- A directory of `.txt`/`.json`/`.yaml`/… files is now enumerated and classified
  instead of skipped with a warning; unregistered suffixes/MIME (and legacy `.doc`,
  ADR-0009) are still rejected with `UnsupportedFormatError`.
- **Size guard for very large text/log files is out of scope** and deferred to a
  follow-up: an unbounded log could produce a very large classifier prompt. Not
  addressed here.
