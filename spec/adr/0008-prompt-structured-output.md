# ADR-0008 — Prompt shape & structured output

Status: accepted

## Context

LLM classification ([[0001-llm-based-classification]]) with Haiku 4.5 ([[0002-model-haiku-4-5]]), single-label with a reserved `unknown` ([[0005-confidence-self-consistency]]). Because confidence is derived by self-consistency (classify each document N times, tally agreement), **each individual call must return exactly one category label that is (a) guaranteed to be in the allowed set and (b) a clean, canonical token comparable across the N runs.** Freeform text breaks agreement tallying (`Finance` vs `finance` vs a full sentence). The category set is known before execution, parsed from a config-controlled Markdown file. Logprobs are unavailable on the Anthropic API.

## Decision

1. **Constrain output with structured outputs** — `output_config.format` with a JSON schema whose `category` field is an `enum` of the defined categories **plus `unknown`**. The enum is **built once at run start** from the parsed, config-controlled category set. The reply is exactly `{"category": "<enum value>"}`.
2. **Label-only** — no per-call reasoning/chain-of-thought field in the schema.
3. **Prompt layout for caching** — the static block (category definitions + few-shot examples + instructions), identical on every call in a run, goes **first** as a prompt-cache prefix; the variable document text goes **last**.
4. **Variation via `temperature`** — self-consistency needs the N runs to vary; Haiku 4.5 supports `temperature`, so use it to produce controlled variation across runs.

## Alternatives

- **Strict tool call** (a `classify` tool with an enum param, `strict: true`) — equally valid output guarantee, but it's the tool-use pattern; more machinery than a single-label classifier needs.
- **Freeform reply + parse** — rejected: does not guarantee an in-set label and produces non-canonical tokens that corrupt agreement tallying.
- **Reason-then-label schema** (`{reasoning, category}`) — more accurate per vote, but adds output tokens on **every** one of the N calls; rejected because self-consistency's N-sample voting already provides robustness, making per-call CoT a poor cost trade here.

## Tradeoffs

- **Gain:** every call returns a guaranteed in-set, canonical label → trivial and correct agreement tallying for [[0005-confidence-self-consistency]]; the cached static prefix cuts the cost of N× calls (cache reads ~10% of full input price); label-only is the cheapest vote shape.
- **Give up:** no per-call chain-of-thought (accepted — mitigated by N-sample voting); the enum must be rebuilt per run (cheap — once at startup); a new/changed category set recompiles the schema (one-time cost, cached ~24h).

## Consequences

- Enum construction: parse categories at startup → `[...categories, "unknown"]` → schema built once, reused for all N × document calls.
- `temperature` value and `N` are related tunables to pin during implementation planning (too low → little variation, inflated agreement; too high → noise).
- Prompt caching depends on the static prefix staying byte-identical across calls — keep any per-call/volatile content (e.g. the document text) strictly after the cached block.
- **Open implementation detail:** documents whose extracted text exceeds the context window (Haiku 4.5: 200K tokens) need a handling strategy (chunk / truncate / summarize) — pin during implementation planning.
