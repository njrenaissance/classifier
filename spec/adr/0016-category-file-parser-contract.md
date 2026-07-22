# ADR-0016 — Category file conforms to the parser contract; code owns the output format

Status: accepted

## Context

The classification label set comes from a Markdown file ([ADR-0001](0001-llm-based-classification.md), [ADR-0008](0008-prompt-structured-output.md)), parsed by A1 (`categories.py`) into the runtime enum. A production taxonomy (`taxonomy.md`, criminal-defense) was drafted in a **different, incompatible shape**, and its companion schema draft proposed treating "the taxonomy file *is* the prompt" — the model returns `{label, confidence, reasoning}` from a single call. Three concrete conflicts:

1. **Format.** The A1 parser expects `## Name` (H2) category headings with **bullet** few-shot examples and prose descriptions. `taxonomy.md` uses `` ### `label` `` (H3, backtick labels) with **blockquote** examples, plus `## Instructions` / `## Categories` / `## Edge Cases` sections. Fed to the current parser it would treat *Instructions* / *Edge Cases* as categories, find no bullet examples, and raise `CategoryFileError` — while ignoring every real (H3) category.
2. **Catch-all collision.** `taxonomy.md` defines `other`; the code reserves a built-in `unknown` ([ADR-0005](0005-confidence-self-consistency.md)). Both would coexist as two catch-alls.
3. **Output contract.** `taxonomy.md`'s `{label, confidence, reasoning}` instruction contradicts label-only structured output ([ADR-0008](0008-prompt-structured-output.md)) and self-consistency-derived confidence ([ADR-0005](0005-confidence-self-consistency.md)), both of which the project chose to keep. The enforced schema field is `category`, not `label`.

## Decision

The **code owns the output contract; the category file owns only label definitions + few-shot examples.** Specifically:

- The category file conforms to the **A1 parser contract**: one `## <name>` per category, description prose, `-` bullet examples. Rich guidance (edge cases, disambiguation, negative examples) is folded **into each category's description/examples**, not into out-of-band `##` sections the parser would misread.
- The **single catch-all is the reserved `unknown`** ([ADR-0005](0005-confidence-self-consistency.md)); the taxonomy's `other` is renamed to `unknown` (the parser already dedups a literal `unknown` into the enum's reserved slot).
- The file carries **no output-format, confidence, or reasoning instructions** — confidence is the self-consistency agreement rate ([ADR-0005](0005-confidence-self-consistency.md)) and the shape is enforced by the `output_config` enum schema ([ADR-0008](0008-prompt-structured-output.md)). The classifier renders its own instruction block from the parsed categories.

## Alternatives

- **Extend the parser to consume `taxonomy.md` as-authored** (H3 + backtick labels + blockquote examples + skip `Instructions`/`Edge Cases`) — lets a domain expert write the richer format, but is a real parser change and invites the "taxonomy is the prompt" model to leak the output contract back into the file. A reasonable *future* ADR, but not chosen now: it widens scope and re-opens ADR-0008's ownership boundary.
- **Adopt "taxonomy is the prompt" + model-reported confidence** — matches the drafted file literally, but discards the self-consistency and structured-output enforcement the project chose to keep ([ADR-0005](0005-confidence-self-consistency.md) / [ADR-0008](0008-prompt-structured-output.md)). Rejected.
- **Let `other` and `unknown` coexist** — two catch-alls, ambiguous downstream ("which one means no-fit?"). Rejected.

## Tradeoffs

- **Gain:** one authoring format, one catch-all, one owner of the output contract; the existing A1 parser, structured output, and self-consistency all keep working unchanged; classification stays source- and taxonomy-agnostic.
- **Give up:** the criminal-defense `taxonomy.md` must be **rewritten** to the parser format (H2 headings, bulletised examples, `other` → `unknown`, JSON/confidence/reasoning block removed); some blockquote/edge-case prose is reshaped into descriptions rather than free-form sections.

## Consequences

- `taxonomy.md` is (re)authored to the A1 contract in a follow-up implementation issue — **not landed as-is**; the parser-format `taxonomy.md` becomes the production category file the classifier job loads at startup.
- No code change to `categories.py`, `classifier.py`, or `self_consistency.py` — the decision is precisely to keep them as the contract.
- The taxonomy **version** line is recorded in `processing_log.metadata` for traceability ([ADR-0013](0013-postgresql-state-store.md)).
