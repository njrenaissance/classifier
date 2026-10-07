# ADR-0022 — Inference layer: OpenAI Decisions API

Status: proposed (blocked on general availability and account access — see Context)

## Context

Classification ([0001](0001-llm-based-classification.md)) currently calls Claude Haiku 4.5 through the Anthropic Messages API, directly or via Microsoft Foundry ([0002](0002-model-haiku-4-5.md), [0016](0016-foundry-inference-provider.md)). The per-call contract is a single label from a fixed set, enforced by structured output ([0008](0008-prompt-structured-output.md)), with confidence derived from N-run self-consistency ([0005](0005-confidence-self-consistency.md)).

OpenAI has announced the [Decisions API](https://tech.ifeng.com/c/8wpIOhwDA3L), which returns one answer from a developer-supplied finite list instead of generating text. Its stated fit is routing and classification, which matches our per-call contract. Its status is not yet suitable for a production dependency:

- It is in **limited preview**. One developer reported a 403 saying the API is not enabled for the account, and documentation pages returned 404 ([eesel.ai](https://www.eesel.ai/de/blog/openai-decisions-api-erklaert)).
- **Pricing, rate limits, and a confidence signal are unpublished.** Secondary coverage disagrees on whether it returns a probability-like score or a confidence value ([NYU RITS](https://rits.shanghai.nyu.edu/ai/openais-decisions-api-picks-answers-instead-of-writing-them/)). Neither is confirmed by OpenAI.
- Its latency claim (~150 ms per decision) is OpenAI's own figure, with no independent measurement yet.
- It is a specialized variant of an OpenAI model, not a Claude model. It is not hosted in Azure, so the in-tenant, managed-identity rationale in [0016](0016-foundry-inference-provider.md) does not carry over unless the API is also reachable through Azure.

## Decision

Adopt the **OpenAI Decisions API as the inference layer** for classification. The classification core keeps its injected-client shape ([0016](0016-foundry-inference-provider.md)), with the client wrapping the Decisions API. Each call receives the category list plus `unknown` as the allowed answers, and returns exactly one of them, preserving the [0008](0008-prompt-structured-output.md) guarantee that every answer is in-set and canonical.

This ADR does **not** take effect until the probe below passes. On acceptance it supersedes [0002](0002-model-haiku-4-5.md) (model) and [0016](0016-foundry-inference-provider.md) (provider selection). Until then, the Claude path remains the working implementation.

## Alternatives

- **Keep Claude Haiku 4.5 via Anthropic or Foundry.** Working today and validated against the probe in [0016](0016-foundry-inference-provider.md). Rejected by the decision to move inference to OpenAI.
- **OpenAI Structured Outputs on a general chat model.** Available now and GA, and gives an in-set label from a JSON schema enum. Not chosen: it is a generation path, not a purpose-built decision endpoint, and it leaves the same confidence gap as today.
- **Wait for GA before deciding.** Would avoid building against a preview. Not chosen because the decision is made; the cost of waiting is carried by the probe gate below.

## Tradeoffs

- **Gain:** a purpose-built single-answer endpoint that matches the classification contract; OpenAI's claimed latency is well below a generation call; a single vendor for inference.
- **Give up:** GA status and published pricing, prompt caching ([0008](0008-prompt-structured-output.md)), the managed-identity/Azure boundary ([0016](0016-foundry-inference-provider.md)) unless Azure access exists, and a Claude model choice ([0002](0002-model-haiku-4-5.md)).

## Consequences

- **Probe before acceptance (gate).** Confirm all of the following against a live account:
  1. The API is enabled for our account, with a stable endpoint and documented schema.
  2. Whether it returns a confidence value. If not, the self-consistency design in [0005](0005-confidence-self-consistency.md) must be re-run on it: N calls per document at the new price and latency.
  3. That `unknown` can be supplied as an allowed answer and is honored.
  4. Per-call cost and rate limits, to re-check the cost model in [0002](0002-model-haiku-4-5.md) and [0005](0005-confidence-self-consistency.md).
  5. Whether it is reachable under managed identity or only via API key, and whether the Azure/in-tenant boundary in [0016](0016-foundry-inference-provider.md) still holds.
- **Config:** `CLASSIFIER_PROVIDER` gains an `openai` value; an `openai` nested settings section holds the API key or identity config. `ANTHROPIC_*` and `CLASSIFIER_FOUNDRY_*` remain until the superseding work is complete.
- **Prompt caching and the static-prefix layout in [0008](0008-prompt-structured-output.md) no longer apply** to this path. The static-prefix design must be re-checked against whatever the Decisions API accepts.
- **Dependency:** the project is blocked on preview access. If the API stays in preview, the Claude path is the fallback, and this ADR should be rejected rather than left proposed.
