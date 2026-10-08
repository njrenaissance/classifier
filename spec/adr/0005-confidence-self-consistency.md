# ADR-0005 — Confidence from API probabilities; reserved `unknown` category

Status: accepted (amended; supersedes the self-consistency decision of the original ADR-0005)

## Context

Each classification needs a confidence signal so low-confidence results can be routed to human review, and documents that fit no category must not be force-fit into a real one.

The original decision used self-consistency: N runs per document, with the agreement rate as confidence. That costs N× the API calls, and the agreement rate is only a proxy for confidence. The classifier is moving to a Jev-style inference API that returns deterministic per-option probabilities for a single call (see [0022](0022-openai-decisions-api-inference.md)), so self-consistency is no longer needed.

## Decision

Two coupled decisions:

1. **Reserved `unknown` category.** In addition to the categories defined in the Markdown file, there is always a built-in `unknown` bucket. The model assigns `unknown` when no defined category fits; it is never forced to guess a real category.
2. **Confidence from the API probability.** Each document is classified with one call. `confidence` is the deterministic probability the inference API assigns to the emitted category. No N-run voting, tie rule, or threshold applies.

## History

The original amendment-free decision was self-consistency: N runs, agreement rate as confidence, a tie or a rate at or below a threshold resolving to `unknown`. It was superseded because it costs N× calls and the agreement rate is not a calibrated probability.

## Alternatives

- **Self-consistency (N runs, agreement rate)** — rejected: N× cost and latency; agreement rate is a proxy, not a probability.
- **Logprob-based scoring** — rejected: the Anthropic Messages API exposes no logprobs.
- **Model self-reported numeric confidence** — rejected: LLM self-confidence is poorly calibrated.

## Tradeoffs

- **Gain:** one call per document; probabilities are deterministic for the same input; `unknown` gives a clean escape hatch.
- **Give up:** trust in the vendor's probability calibration, which is unverified and must be probed before acceptance of [0022](0022-openai-decisions-api-inference.md).

## Consequences

- `confidence` is a column in the output CSV ([0004](0004-csv-file-output.md)) and in the `documents` table, sourced from the API probability.
- The N and confidence-threshold tunables are removed.
- Cost and latency return to one call per document.
