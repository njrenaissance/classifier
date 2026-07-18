"""Self-consistency voting + confidence (B2 / ADR-0005).

The B1 classifier core (:mod:`classifier`) makes one call and returns one label.
This module is the downstream layer that turns that single call into a confidence
signal: it runs the classifier **N times** over one document and votes.

Per ADR-0005, confidence is the **agreement rate** of the modal label
(``modal_count / N``); the emitted category is the modal label, unless there is a
**tie** for the top count or the agreement rate is **at or below** the configured
threshold, in which case it resolves to the reserved ``unknown``. Run-to-run
variation comes from ``temperature`` (ADR-0008), which already lives on the
injected :class:`~classifier.Classifier`; this layer only owns N and the threshold.
"""

from collections import Counter
from dataclasses import dataclass

from categories import UNKNOWN_CATEGORY, CategorySet
from classifier import Classifier, create_classifier
from config import Settings, get_settings


@dataclass(frozen=True)
class Verdict:
    """One document's self-consistency outcome: a modal category + confidence.

    Deliberately source-agnostic (no ``filename``): a caller combines a
    :class:`Verdict` with a name to build a
    :class:`~writer.ClassificationResult` for CSV output (ADR-0004).
    """

    category: str  # a real category name or the reserved "unknown"
    confidence: float  # agreement rate of the modal label in [0.0, 1.0] — ADR-0005


class SelfConsistencyClassifier:
    """Votes over N single-call classifications to produce a :class:`Verdict`.

    The inner :class:`~classifier.Classifier` is injected so the network boundary
    stays fakeable in tests, exactly as the core classifier is.
    """

    def __init__(self, classifier: Classifier, *, n: int, confidence_threshold: float) -> None:
        if n < 1:
            raise ValueError(f"Self-consistency N must be >= 1, got {n}.")
        self._classifier = classifier
        self._n = n
        self._confidence_threshold = confidence_threshold

    def classify(self, document_text: str) -> Verdict:
        """Classify ``document_text`` N times and return the voted :class:`Verdict`.

        Any :class:`~errors.ClassificationError` from a single run propagates — a
        failed run fails the document rather than voting on a partial tally.
        """
        labels = [self._classifier.classify(document_text) for _ in range(self._n)]
        return self._vote(labels)

    def _vote(self, labels: list[str]) -> Verdict:
        """Turn N labels into a modal category + agreement-rate confidence."""
        counts = Counter(labels)
        (modal_label, modal_count), *rest = counts.most_common()
        confidence = modal_count / self._n
        is_tie = bool(rest) and rest[0][1] == modal_count
        if is_tie or confidence <= self._confidence_threshold:
            return Verdict(category=UNKNOWN_CATEGORY, confidence=confidence)
        return Verdict(category=modal_label, confidence=confidence)


def create_self_consistency_classifier(
    categories: CategorySet, settings: Settings | None = None
) -> SelfConsistencyClassifier:
    """Build a :class:`SelfConsistencyClassifier` with N and temperature from config.

    Mirrors :func:`classifier.create_classifier`: ``temperature`` is sourced by the
    inner classifier, while N and the confidence threshold come from settings.
    """
    settings = settings or get_settings()
    classifier = create_classifier(categories, settings)
    return SelfConsistencyClassifier(
        classifier,
        n=settings.self_consistency_n,
        confidence_threshold=settings.confidence_threshold,
    )
