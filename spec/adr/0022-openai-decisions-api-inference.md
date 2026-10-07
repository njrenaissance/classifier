# ADR-0022 — Inference layer: TypeSafe Jev

Status: proposed (gated on early-access access and a probe — see Consequences)

## Context

Classification ([0001](0001-llm-based-classification.md)) currently calls Claude Haiku 4.5 through the Anthropic Messages API, directly or via Microsoft Foundry ([0002](0002-model-haiku-4-5.md), [0016](0016-foundry-inference-provider.md)). The per-call contract is a single label from a fixed set, enforced by structured output ([0008](0008-prompt-structured-output.md)), with confidence derived from N-run self-consistency ([0005](0005-confidence-self-consistency.md)).

We want a purpose-built decision model rather than a text-generating one. Two candidates:

- **OpenAI Decisions API** returns one answer from a finite list. It is in limited preview, with a reported 403 for accounts not enabled and 404 documentation pages, and pricing and confidence are unpublished ([eesel.ai](https://www.eesel.ai/de/blog/openai-decisions-api-erklaert), [NYU RITS](https://rits.shanghai.nyu.edu/ai/openais-decisions-api-picks-answers-instead-of-writing-them/)). It is not hosted in Azure.
- **TypeSafe Jev** is a "System One" decision model that returns a typed choice from a defined answer space, with Choice and Score question types ([Composio guide](https://composio.dev/content/typesafe-jev-guide-and-best-practical-usecases)). It is in early access behind a waitlist. Reported input pricing is about $0.042 per million tokens with output free ([eesel.ai](https://www.eesel.ai/blog/typesafe-jev-pricing)), and it has a reported 32,000-token context window ([LiteLLM](https://docs.litellm.ai/blog/typesafe_jev)). It is also listed on [OpenRouter](https://openrouter.ai/typesafe/jev-1.13).

Speed and cost claims for both come from the vendors, and no independent replication was found. I could not locate TypeSafe's official pricing or docs pages.

## Decision

Adopt **TypeSafe Jev as the inference layer** for classification, conditional on the probe below. The classification core keeps its injected-client shape ([0016](0016-foundry-inference-provider.md)), with the client wrapping Jev. Each call receives the category list plus `unknown` as the answer space and returns exactly one of them, preserving the [0008](0008-prompt-structured-output.md) guarantee that every answer is in-set and canonical.

On acceptance this supersedes [0002](0002-model-haiku-4-5.md) (model) and [0016](0016-foundry-inference-provider.md) (provider selection). Until then, the Claude path remains the working implementation.

## Alternatives

- **OpenAI Decisions API.** Same purpose-built shape. Rejected for now: limited preview with no visible access path, and no published pricing. Revisit if it reaches GA.
- **Keep Claude Haiku 4.5 via Anthropic or Foundry.** Working and validated against the probe in [0016](0016-foundry-inference-provider.md). Remains the fallback.
- **OpenAI Structured Outputs on a general chat model.** GA and gives an in-set enum label. Not chosen: a generation path, not a decision endpoint.

## Tradeoffs

- **Gain:** a typed decision model matching the classification contract; a reported price far below generation models; available through an API and OpenRouter.
- **Give up:** GA status and vendor-verified claims; the 32K context window versus Haiku's 200K (affects long documents, see [0008](0008-prompt-structured-output.md)); prompt caching; the managed-identity/Azure boundary in [0016](0016-foundry-inference-provider.md) unless Jev is reachable in-tenant; a Claude model.

## Consequences

- **Data-handling gate (blocks acceptance).** Extracted document text goes to the provider, so this is also a data-residency decision. Status per a third-party review (verified September 19, 2026):

  | Item | Status | Gap |
  |---|---|---|
  | No training on inputs | Vendor-stated | Confirm in contract |
  | Retention | "As long as reasonably necessary"; no number | Need a fixed period; zero retention only via sales |
  | Residency | US only; no region choice | No EU option. Blocks EU/UK data unless accepted |
  | DPA: SCCs, UK Addendum, 72h breach notice, annual audit | Published | Reviewed; acceptable |
  | SOC 2 Type II | Reported by a third-party index | Unconfirmed. Request the report |
  | HIPAA BAA, ISO 27001 | Not reported | Needed only if such data is in scope |

  Before real documents are sent, get in writing: a fixed retention period (or zero-retention terms), the SOC 2 Type II report, and a residency answer for any EU/UK data. Until then, only synthetic or public documents go to Jev. The current Foundry path ([0016](0016-foundry-inference-provider.md)) keeps inference in-tenant and is the safety baseline this ADR must not regress.
- **Probe before acceptance (gate).** Against a live early-access account, confirm:
  1. Access is granted, with a stable endpoint and documented schema.
  2. How `unknown` is expressed in the answer space and that it is honored.
  3. Whether a confidence or probability value is returned. Jev's Choice questions report probabilities per option per the Composio guide; if they are usable, self-consistency in [0005](0005-confidence-self-consistency.md) may be unnecessary. If not, re-cost N calls per document.
  4. Actual per-call cost, latency, and rate limits, against the cost model in [0002](0002-model-haiku-4-5.md) and [0005](0005-confidence-self-consistency.md).
  5. How the 32K context limit is handled for long extracted documents (chunk, truncate, or summarize; see [0008](0008-prompt-structured-output.md)).
  6. Whether it is reachable under managed identity or only via API key, and whether the in-tenant boundary in [0016](0016-foundry-inference-provider.md) still holds.
- **Config:** `CLASSIFIER_PROVIDER` gains a `jev` value with a nested `jev` settings section. `ANTHROPIC_*` and `CLASSIFIER_FOUNDRY_*` remain until superseding work is complete.
- **Prompt caching and the static-prefix layout in [0008](0008-prompt-structured-output.md)** do not apply to this path and must be re-checked against Jev's request format.
- **Dependency:** blocked on early-access approval. If access is not granted in a reasonable window, reject this ADR and keep the Claude path.
