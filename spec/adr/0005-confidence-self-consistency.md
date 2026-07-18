# ADR-0005 — Confidence via self-consistency; reserved `unknown` category

Status: accepted

## Context

Each classification needs a confidence signal so low-confidence results can be routed to human review, and documents that fit no category must not be force-fit into a real one. The natural approach — token logprobs — is **not available**: the Anthropic Messages API (used by `claude-haiku-4-5`, [[0002-model-haiku-4-5]]) exposes no logprobs parameter or response field.

## Decision

Two coupled decisions:

1. **Reserved `unknown` category.** In addition to the categories defined in the Markdown file, there is always a built-in `unknown` bucket. The model assigns `unknown` when no defined category fits; it is never forced to guess a real category.
2. **Confidence via self-consistency.** Classify each document **N times** (N configurable, default ~5) and use the **agreement rate** as the confidence score (5/5 → 1.0, 3/5 → 0.6). The emitted category is the modal label across the N runs; a tie, or a modal count at/below a threshold, resolves to `unknown`.

## Alternatives

- **Logprob-based scoring** — rejected: not implementable, the Anthropic API exposes no logprobs.
- **Model self-reported numeric confidence** (0–1 in structured output) — 1 call/doc, cheapest, but LLM self-confidence is poorly calibrated.
- **Coarse high/med/low bucket** — 1 call/doc, honest about granularity, but coarse.

## Tradeoffs

- **Gain:** an empirical, reproducible confidence grounded in observed model behaviour rather than uncalibrated self-report; `unknown` gives a clean escape hatch.
- **Give up:** **N× the API calls** per document (acceptable at <100 files with cheap Haiku — [[0002-model-haiku-4-5]]); agreement rate is a proxy for confidence, not a true calibrated probability.

## Consequences

- `confidence` is a column in the output CSV ([[0004-csv-file-output]]).
- N and the `unknown` tie/threshold rule are tunables to pin during implementation planning.
- Cost and latency scale with N — revisit if N or volume grows materially.
