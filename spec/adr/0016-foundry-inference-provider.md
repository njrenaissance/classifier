# ADR-0016 — Selectable inference provider: Anthropic API or Microsoft Foundry

Status: accepted

## Context

The classification core takes an injected client and only `create_classifier()`
constructs one ([[0001-llm-based-classification]], [[0008-prompt-structured-output]]).
Until now that client was always the first-party `anthropic.Anthropic`, requiring an
`ANTHROPIC_API_KEY`.

The v2 cloud pipeline runs the classifier unattended **inside Azure Container Apps**
([0012](0012-cloud-two-job-pipeline.md)), authenticating to Graph, Key Vault, and
PostgreSQL with **managed identity** ([0013](0013-postgresql-state-store.md),
[0015](0015-graph-authenticated-download.md)). Sending inference to the first-party
Anthropic API from there means an Anthropic key egresses the tenant and lives outside
the managed-identity story that governs everything else. Microsoft Foundry (formerly
Azure AI Foundry) serves the same Claude models through the same `messages.create`
surface (`anthropic.AnthropicFoundry`), inside the Azure trust boundary.

A latent bug forces the issue: `Settings.anthropic_api_key` was a required field with
no default, so a Foundry-only job would crash in `get_settings()` for lack of an
Anthropic key it never uses.

## Decision

Make the inference provider **selectable** via `CLASSIFIER_PROVIDER`
(`anthropic` | `foundry`, default `anthropic`), keeping the classification core
unchanged:

- **Per-provider nested settings.** `Settings` gains `anthropic` and `foundry` nested
  `BaseSettings` (attached with `default_factory`). Each provider's credentials default
  to `None`/absent so the *unselected* section always loads; a `model_validator`
  requires only the **selected** provider's credentials (`ANTHROPIC_API_KEY` for
  anthropic; `ANTHROPIC_FOUNDRY_RESOURCE` for foundry). This is the prescribed fix in
  `.claude/standards/configuration.md` and resolves the required-key bug.
- **`create_classifier()` branches on the provider**; `Classifier` classification logic
  is untouched (it gains only an optional `model` kwarg defaulting to the pinned
  `claude-haiku-4-5`, and continues to accept the injected client — `AnthropicFoundry`
  is a subclass of `anthropic.Anthropic`, so the type is unchanged).
- **Foundry auth mode is explicit.** `CLASSIFIER_FOUNDRY_USE_MANAGED_IDENTITY=true`
  selects Entra ID / managed identity — an azure-identity bearer-token provider
  (`get_bearer_token_provider(DefaultAzureCredential(), scope)`) passed as
  `azure_ad_token_provider`, the same managed-identity model as the rest of the v2
  design (production sets this). Otherwise an `ANTHROPIC_FOUNDRY_API_KEY` is required.
  The `Settings` validator enforces exactly one at startup, so a forgotten key fails
  loudly rather than silently attempting a doomed managed-identity call. `azure.identity`
  is imported lazily so the API-key and Anthropic paths never need it.
- **Model id is a per-provider setting** (`CLASSIFIER_ANTHROPIC_MODEL` /
  `CLASSIFIER_FOUNDRY_MODEL`, both defaulting to `claude-haiku-4-5`). Foundry's model-id
  convention is unconfirmed until probed; making it config means the probe outcome is a
  config value, not a code change.
- Foundry credentials reuse the SDK's own env var names (`ANTHROPIC_FOUNDRY_RESOURCE`,
  `ANTHROPIC_FOUNDRY_API_KEY`), matching the existing precedent that `ANTHROPIC_API_KEY`
  is read unprefixed to align with the SDK's own resolution.

## Alternatives

- **Replace Anthropic with Foundry.** Rejected — local dev and CI must keep working with
  the direct API and no Azure credentials ([0003](0003-cli-batch-interface.md)/
  [0004](0004-csv-file-output.md)), and coexistence preserves A/B of a suspected Foundry
  regression against first-party.
- **Both credentials always required.** Rejected — it is the bug: a Foundry-only Azure
  job has no Anthropic key, and a local run has no Foundry resource.
- **API-key-only Foundry (no managed identity).** Rejected — it would leave the
  classifier job carrying a stored secret while the rest of the stack uses managed
  identity; the SDK supports `azure_ad_token_provider`, so there is no reason to.
- **Pin one shared model constant for both providers.** Rejected — Foundry's model-id
  format is unconfirmed; a single constant cannot absorb a per-provider difference
  without a code change.

## Tradeoffs

- **Gain:** in-tenant inference under managed identity with no key egress; the
  required-key bug is fixed; local/CI Anthropic path is unchanged; provider and model are
  config, not code.
- **Give up:** a new `azure-identity` runtime dependency; two credential shapes to
  document; the Foundry path cannot be fully validated without a live Foundry resource
  (see below).

## Consequences

- New config: `CLASSIFIER_PROVIDER`, nested `anthropic`/`foundry` sections, and their env
  vars; `.env.example` covers both providers. `settings.anthropic_api_key` becomes
  `settings.anthropic.api_key`.
- `azure-identity` added to runtime dependencies (used only on the managed-identity
  path).
- **Probe still owed against a real Foundry resource** before the classifier job is
  deployed ([0012](0012-cloud-two-job-pipeline.md)): confirm `claude-haiku-4-5` is served
  and its exact id format, that `output_config.format` and `cache_control`
  ([0008](0008-prompt-structured-output.md)) behave, and the managed-identity token
  scope. Managed-identity **support** is already confirmed via the SDK.
- **ADR-0002 stays `accepted`.** If the probe finds Haiku is not served on Foundry, the
  per-run cost rises materially and [ADR-0002](0002-model-haiku-4-5.md) must be
  **superseded** (not amended) — this ADR does not pre-empt that decision.
