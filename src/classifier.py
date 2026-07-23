"""Classifier core — single structured-output classification call (B1).

One call, one label. Given extracted document text and the runtime category enum
(the parsed categories plus the reserved ``unknown``), ask Claude Haiku 4.5 for
exactly one in-set category label using structured outputs (ADR-0008 / ADR-0002).

The reply is constrained by ``output_config.format`` to a JSON schema whose
``category`` field is an ``enum`` of the allowed labels, so the model can never
invent or paraphrase a label. The static category block (definitions + few-shot
examples + instructions) is placed first, as a prompt-cache prefix, and the
variable document text goes last — both the block and the schema are built once
per :class:`Classifier` so the cached prefix stays byte-identical across a run.

Self-consistency / N-run voting and confidence (ADR-0005) live downstream: a
caller invokes :meth:`Classifier.classify` N times and tallies the labels. This
module owns only the single call.
"""

import json

import anthropic
from anthropic.types import Message, TextBlock

from categories import CategorySet
from config import FoundrySettings, Settings, get_settings
from errors import ClassificationError

MODEL = "claude-haiku-4-5"  # ADR-0002 — pinned; never append a date suffix.
_MAX_TOKENS = 64  # label-only reply (e.g. '{"category":"invoice"}') is tiny.


def _render_category_block(categories: CategorySet) -> str:
    """Render the static system prompt: definitions + few-shot examples.

    Ordered and free of volatile content so the rendered text is byte-identical
    on every call in a run, which is what keeps the prompt-cache prefix valid.
    """
    lines = [
        "You are a document classifier. Classify the document into exactly one of the categories below.",
        "",
        "# Categories",
    ]
    for category in categories.categories:
        lines.append("")
        lines.append(f"## {category.name}")
        if category.description:
            lines.append(category.description)
        lines.append("Examples:")
        lines.extend(f"- {example}" for example in category.examples)
    lines.append("")
    lines.append(
        "If the document does not clearly fit any category, respond with "
        "'unknown'. Respond with exactly one category label."
    )
    return "\n".join(lines)


class Classifier:
    """Classifies document text into one in-set category label via one API call.

    The static category block and the enum schema are built once at construction
    so the prompt-cache prefix stays byte-identical across every call (ADR-0008).
    The Anthropic client is injected so the network boundary can be faked in tests.
    """

    def __init__(
        self, categories: CategorySet, client: anthropic.Anthropic, *, model: str = MODEL, temperature: float
    ) -> None:
        self._client = client
        self._model = model
        self._temperature = temperature
        self._enum_values = categories.enum_values
        self._system_block = _render_category_block(categories)
        self._schema: dict[str, object] = {
            "type": "object",
            "properties": {"category": {"type": "string", "enum": list(self._enum_values)}},
            "required": ["category"],
            "additionalProperties": False,
        }

    def classify(self, document_text: str) -> str:
        """Return the single in-set category label for ``document_text``.

        Raises :class:`~errors.ClassificationError` if the call fails or the reply
        cannot be turned into an allowed label.
        """
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=_MAX_TOKENS,
                temperature=self._temperature,
                system=[{"type": "text", "text": self._system_block, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": document_text}],
                output_config={"format": {"type": "json_schema", "schema": self._schema}},
            )
        except anthropic.APIError as err:
            raise ClassificationError(f"Classification API call failed: {err}") from err
        return self._extract_label(response)

    def _extract_label(self, response: Message) -> str:
        """Pull the one allowed label out of a structured-output response."""
        if response.stop_reason in ("refusal", "max_tokens"):
            raise ClassificationError(f"Classification did not complete: stop_reason={response.stop_reason!r}")
        text = next((block.text for block in response.content if isinstance(block, TextBlock)), None)
        if text is None:
            raise ClassificationError("Classification response contained no text block.")
        try:
            label = json.loads(text)["category"]
        except (json.JSONDecodeError, KeyError, TypeError) as err:
            raise ClassificationError(f"Classification response was not the expected JSON: {text!r}") from err
        if not isinstance(label, str) or label not in self._enum_values:
            raise ClassificationError(f"Classification returned an out-of-set label: {label!r}")
        return label


def _build_foundry_client(foundry: FoundrySettings) -> anthropic.AnthropicFoundry:
    """Construct a Foundry client — managed identity if enabled, else API key.

    Authentication mode is explicit (``use_managed_identity``): managed identity
    goes through an Azure AD bearer-token provider (ADR-0015/0016), with
    ``azure.identity`` imported here, lazily, so the API-key and Anthropic paths
    never require it. :class:`~config.Settings` validation guarantees a usable
    credential for the selected mode, so the guard below is unreachable.
    """
    if foundry.use_managed_identity:
        from azure.identity import DefaultAzureCredential, get_bearer_token_provider

        token_provider = get_bearer_token_provider(DefaultAzureCredential(), foundry.token_scope)
        return anthropic.AnthropicFoundry(resource=foundry.resource, azure_ad_token_provider=token_provider)
    if foundry.api_key is None:  # pragma: no cover - Settings validation already guarantees this
        raise ValueError("ANTHROPIC_FOUNDRY_API_KEY is required unless managed identity is enabled.")
    return anthropic.AnthropicFoundry(resource=foundry.resource, api_key=foundry.api_key.get_secret_value())


def _build_client(settings: Settings) -> tuple[anthropic.Anthropic, str]:
    """Pick the inference client and model id for the selected provider.

    The selected provider's credentials are enforced here — not at settings load
    (config.py) — so a job that needs no inference (the walker, migrations) can
    build :class:`~config.Settings` without a provider credential present.
    """
    if settings.provider == "foundry":
        if settings.foundry is None:
            raise ValueError(
                "Provider 'foundry' is selected but Foundry is not configured; "
                "set ANTHROPIC_FOUNDRY_RESOURCE and a credential."
            )
        return _build_foundry_client(settings.foundry), settings.foundry.model
    if settings.anthropic is None or settings.anthropic.api_key is None:
        raise ValueError("ANTHROPIC_API_KEY is required for provider 'anthropic'.")
    return anthropic.Anthropic(api_key=settings.anthropic.api_key.get_secret_value()), settings.anthropic.model


def create_classifier(categories: CategorySet, settings: Settings | None = None) -> Classifier:
    """Build a :class:`Classifier` with a client for the selected provider."""
    settings = settings or get_settings()
    client, model = _build_client(settings)
    return Classifier(categories, client, model=model, temperature=settings.temperature)
