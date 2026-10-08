"""Classifier factory — the Jev inference seam (ADR-0022).

One document, one call: :meth:`LabelClassifier.classify` returns a
:class:`~classification.Classification` with the category, its deterministic API
probability as confidence, and the raw provider response (ADR-0005, amended).
Claude and Foundry are no longer used for classification (ADR-0002, ADR-0016).
"""

from typing import Protocol

from categories import CategorySet
from classification import Classification
from config import Settings, get_settings
from jev import JevClassifier, JevHttpClient


class LabelClassifier(Protocol):
    """Anything that turns document text into one classification (one call per document)."""

    def classify(self, document_text: str) -> Classification: ...


def _build_jev_classifier(categories: CategorySet, settings: Settings) -> JevClassifier:
    """Build the Jev adapter from its nested settings (ADR-0022).

    The credentials are enforced here rather than at settings load, so a job that
    never classifies (the walker, migrations) needs no Jev configuration.
    """
    if settings.jev is None or not settings.jev.is_configured:
        raise ValueError("Jev is not configured; set CLASSIFIER__JEV_API_KEY and CLASSIFIER__JEV_BASE_URL.")
    assert settings.jev.api_key is not None  # narrowed by is_configured above
    assert settings.jev.base_url is not None
    client = JevHttpClient(api_key=settings.jev.api_key.get_secret_value(), base_url=str(settings.jev.base_url))
    return JevClassifier(categories, client)


def create_classifier(categories: CategorySet, settings: Settings | None = None) -> LabelClassifier:
    """Build the classifier for the configured inference provider (Jev only)."""
    return _build_jev_classifier(categories, settings or get_settings())
