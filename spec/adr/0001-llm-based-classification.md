# ADR-0001 — LLM-based classification

Status: accepted

## Context

`classifier` must assign each document (PDF/DOCX/DOC) to exactly one category from a user-defined set whose definitions and few-shot examples live in a Markdown file. The category set is not fixed in code and can change per deployment. We need a classification method that works from natural-language category definitions + a handful of examples, without a labelled training corpus.

## Decision

Classify documents with an **LLM**, prompting it with the category definitions and few-shot examples drawn from the Markdown file, plus the extracted document text.

## Alternatives

- **Rule-based** (keyword/regex per category) — no training data needed, fully deterministic, but brittle, high-maintenance, and a poor fit for fuzzy/semantic categories.
- **Classical ML** (e.g. TF-IDF + logistic regression / SVM) — cheap at inference, but requires a labelled training set per category set, and re-training whenever the Markdown categories change.

## Tradeoffs

- **Gain:** works directly from prose category definitions + few-shot examples; adapts to a changed category set with no retraining; strong on semantic/fuzzy distinctions.
- **Give up:** non-deterministic outputs; per-document API cost and latency; dependency on an external service; confidence is not natively available (see [[0005-confidence-self-consistency]]).

## Consequences

- Introduces a hard dependency on the Anthropic API and the `anthropic` SDK.
- Model choice is a follow-on decision — see [[0002-model-haiku-4-5]].
- The category Markdown becomes prompt input; how it is rendered into the prompt is an implementation detail to pin during Phase 1.
