"""TypeSafe Jev inference adapter (ADR-0022), built on the ``typesafe-sdk`` package.

Jev answers a decision (one option from a finite list) rather than generating
text. The adapter asks one ``Choice`` question whose criteria are the category
names (with their descriptions) plus ``unknown``. The API returns the chosen
option with a deterministic probability per option; that probability is the
confidence (ADR-0005, amended).
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from typesafe_sdk import Choice, TypeSafeClient, TypeSafeError

from categories import UNKNOWN_CATEGORY, CategorySet
from classification import Classification
from errors import AppError, ClassificationError

MODEL = "typesafe-jev"  # stamped into ``documents.classified_by`` (writer.py).
MAX_INPUT_CHARS = 128_000  # ~32K-token Jev context at ~4 chars/token; to be confirmed by the probe.

_QUESTION_NAME = "category"
_STATE_KEY = "document"
_UNKNOWN_DESCRIPTION = "None of the categories above fit the document."


class JevClientError(AppError):
    """A failure reported by the Jev client (transport, auth, or a malformed response)."""


@dataclass(frozen=True)
class JevResponse:
    """The client's view of one Jev decision.

    ``choice`` is the selected option. ``probabilities`` maps each option to its
    probability. ``raw`` is the complete provider response body, unmodified.
    """

    choice: str
    probabilities: Mapping[str, float]
    raw: dict[str, Any] = field(default_factory=dict)


class JevDecisionClient(Protocol):
    """The seam to the Jev API: pick exactly one criterion for a document."""

    def decide(self, document_text: str, criteria: Mapping[str, str | None]) -> JevResponse: ...


class JevClassifier:
    """Adapts a :class:`JevDecisionClient` to the ``classify(text) -> Classification`` contract.

    Documents longer than ``max_input_chars`` are rejected before any API call
    rather than truncated, so a long document is never silently classified on
    partial text.
    """

    def __init__(
        self, categories: CategorySet, client: JevDecisionClient, *, max_input_chars: int = MAX_INPUT_CHARS
    ) -> None:
        self._client = client
        self._options = categories.enum_values
        self._criteria: dict[str, str | None] = {
            category.name: category.description or None for category in categories.categories
        }
        self._criteria[UNKNOWN_CATEGORY] = _UNKNOWN_DESCRIPTION
        self._max_input_chars = max_input_chars

    def classify(self, document_text: str) -> Classification:
        """Return the chosen category, its probability as confidence, and the raw response.

        Raises :class:`~errors.ClassificationError` if the document is too long,
        the client fails, the choice is outside the answer space, or the choice
        has no valid probability.
        """
        if len(document_text) > self._max_input_chars:
            raise ClassificationError(
                f"Document is {len(document_text)} characters; Jev accepts at most {self._max_input_chars}."
            )
        try:
            response = self._client.decide(document_text, self._criteria)
        except JevClientError as err:
            raise ClassificationError(f"Jev classification failed: {err}") from err
        if response.choice not in self._options:
            raise ClassificationError(f"Jev returned an out-of-set label: {response.choice!r}")
        probability = response.probabilities.get(response.choice)
        if probability is None or not 0.0 <= probability <= 1.0:
            raise ClassificationError(f"Jev returned no valid probability for {response.choice!r}: {probability!r}")
        return Classification(category=response.choice, confidence=float(probability), raw=dict(response.raw))


class JevHttpClient:
    """Jev client backed by the TypeSafe SDK's sync client (one ``Choice`` question per document)."""

    def __init__(self, *, api_key: str, base_url: str) -> None:
        self._client = TypeSafeClient(api_key=api_key, base_url=base_url)

    def decide(self, document_text: str, criteria: Mapping[str, str | None]) -> JevResponse:
        """Ask Jev to choose one criterion for ``document_text``.

        SDK failures are translated to :class:`JevClientError`, chained from the
        original, so the classifier sees one domain failure type.
        """
        question = Choice(
            instructions=f"Which category does `{_STATE_KEY}` belong to?",
            criteria=criteria,
        )
        try:
            response = self._client.system_one(state={_STATE_KEY: document_text}, questions={_QUESTION_NAME: question})
        except TypeSafeError as err:
            raise JevClientError(f"TypeSafe API call failed: {err}") from err
        answer = response.choices.get(_QUESTION_NAME)
        if answer is None:
            raise JevClientError(f"TypeSafe response has no choice answer for {_QUESTION_NAME!r}.")
        body = response.raw_http_response.json()
        if not isinstance(body, dict):
            raise JevClientError("TypeSafe response body is not a JSON object.")
        return JevResponse(choice=answer.choice, probabilities=answer.probabilities, raw=body)
