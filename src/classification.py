"""The per-document classification result contract (ADR-0005 amended, ADR-0022).

One inference call yields one :class:`Classification`: the chosen category, the
deterministic probability the inference API assigned to it, and the complete
provider response, unmodified. ``raw`` is persisted for audit but never logged,
because it may echo document content (see ``.claude/standards/logging.md``).
"""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Classification:
    """One document's classification outcome.

    ``category`` is a real category name or the reserved ``unknown``.
    ``confidence`` is the API probability for ``category``, in ``[0.0, 1.0]``.
    ``raw`` is the provider response exactly as received.
    """

    category: str
    confidence: float
    raw: dict[str, Any]
