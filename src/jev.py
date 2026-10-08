"""TypeSafe Jev inference adapter (ADR-0022).

Jev answers a decision (one option from a finite list) rather than generating
text, so the adapter sends the category enum plus ``unknown`` as the answer
space and accepts only one of those options back. The HTTP client is still a
stub: its request and response schema are only known after early-access access
and the ADR-0022 probe.
"""

from collections.abc import Sequence
from typing import Protocol

from categories import CategorySet
from errors import AppError, ClassificationError

MAX_INPUT_CHARS = 128_000  # ~32K-token Jev context at ~4 chars/token; to be confirmed by the probe.


class JevClientError(AppError):
    """A failure reported by the Jev client (transport, auth, or a malformed response)."""


class JevDecisionClient(Protocol):
    """The seam to the Jev API: pick exactly one option for a document."""

    def decide(self, document_text: str, options: Sequence[str]) -> str: ...


class JevClassifier:
    """Adapts a :class:`JevDecisionClient` to the ``classify(text) -> label`` contract.

    Documents longer than ``max_input_chars`` are rejected before any API call
    rather than truncated, so a long document is never silently classified on
    partial text.
    """

    def __init__(
        self, categories: CategorySet, client: JevDecisionClient, *, max_input_chars: int = MAX_INPUT_CHARS
    ) -> None:
        self._client = client
        self._options = categories.enum_values
        self._max_input_chars = max_input_chars

    def classify(self, document_text: str) -> str:
        """Return the single in-set label for ``document_text``.

        Raises :class:`~errors.ClassificationError` if the document is too long,
        the client fails, or the client returns a label outside the answer space.
        """
        if len(document_text) > self._max_input_chars:
            raise ClassificationError(
                f"Document is {len(document_text)} characters; Jev accepts at most {self._max_input_chars}."
            )
        try:
            label = self._client.decide(document_text, self._options)
        except JevClientError as err:
            raise ClassificationError(f"Jev classification failed: {err}") from err
        if label not in self._options:
            raise ClassificationError(f"Jev returned an out-of-set label: {label!r}")
        return label


class JevHttpClient:
    """Real Jev client. Not implemented until the ADR-0022 probe confirms the API schema."""

    def __init__(self, *, api_key: str, base_url: str) -> None:
        raise NotImplementedError("The Jev HTTP client awaits the ADR-0022 probe; use provider 'anthropic' for now.")

    def decide(self, document_text: str, options: Sequence[str]) -> str:
        raise NotImplementedError
