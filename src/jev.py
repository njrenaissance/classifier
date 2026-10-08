"""TypeSafe Jev inference adapter (ADR-0022).

Stub: signatures only, so the tests in ``tests/test_jev.py`` collect and fail
until the implementation lands after the ADR-0022 probe.
"""

from collections.abc import Sequence
from typing import Protocol

from categories import CategorySet
from errors import AppError

MAX_INPUT_CHARS = 128_000  # ~32K-token Jev context at ~4 chars/token; to be confirmed by the probe.


class JevClientError(AppError):
    """A failure reported by the Jev client (transport, auth, or a malformed response)."""


class JevDecisionClient(Protocol):
    """The seam to the Jev API: pick exactly one option for a document."""

    def decide(self, document_text: str, options: Sequence[str]) -> str: ...


class JevClassifier:
    """Adapts a :class:`JevDecisionClient` to the ``classify(text) -> label`` contract."""

    def __init__(
        self, categories: CategorySet, client: JevDecisionClient, *, max_input_chars: int = MAX_INPUT_CHARS
    ) -> None:
        raise NotImplementedError

    def classify(self, document_text: str) -> str:
        raise NotImplementedError


class JevHttpClient:
    """Real Jev client. Implemented after early-access access and the probe."""

    def __init__(self, *, api_key: str, base_url: str) -> None:
        raise NotImplementedError

    def decide(self, document_text: str, options: Sequence[str]) -> str:
        raise NotImplementedError
