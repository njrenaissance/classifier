# ADR-0002 — Model: Claude Haiku 4.5

Status: accepted

## Context

Having chosen LLM-based classification ([[0001-llm-based-classification]]), we must pick a specific Claude model. The task is single-label classification into a defined set with few-shot examples — a bounded task, not open-ended reasoning. Volume is <100 files per run, but confidence is derived by classifying each document multiple times (see [[0005-confidence-self-consistency]]), so the effective call count is N× the file count.

## Decision

Use **`claude-haiku-4-5`** for the classification calls.

## Alternatives

- **Sonnet 5** (`claude-sonnet-5`) — higher quality on nuanced/subtle taxonomies; ~3× the input / ~3× the output price of Haiku.
- **Opus 4.8** (`claude-opus-4-8`) — most capable, highest cost; overkill for single-label classification.

## Tradeoffs

- **Gain:** lowest cost ($1/$5 per 1M tokens) and fastest — important because self-consistency multiplies the call count; well-suited to a constrained classification task.
- **Give up:** less headroom on genuinely subtle or ambiguous category boundaries than Sonnet/Opus.

## Consequences

- If accuracy proves insufficient on a real category set, escalate to Sonnet 5 — record that as a superseding ADR rather than editing this one.
- Model ID is pinned as `claude-haiku-4-5`; do not append a date suffix.
- Cost scales with N (self-consistency runs) × document count × tokens; keep an eye on it as N or volume grows.
