"""Live-fire baseline: classify the committed sample corpus against the real API.

This promotes issue #42's "a representative document per category classifies to the
intended label" acceptance criterion into a repeatable, committed test over the
`samples/` corpus. It makes **real** inference calls (N per document) through the
configured provider, so it is marked ``integration`` and **skips** unless a provider
is configured — CI (no credentials) and the ``-m unit`` pre-commit run never touch
the network here.

Two of the original NYPD samples were dropped as un-extractable scanned PDFs
(``Command Log``, ``ECMS Access Log``): the extraction stack has no OCR
(ADR-0006/0009), so image-only documents are out of scope for this baseline. Every
sample below has a real text layer and classified at full agreement in the
reference run.
"""

from pathlib import Path

import pytest

from categories import parse_category_file
from config import get_settings
from extraction import extract_text
from self_consistency import SelfConsistencyClassifier, create_self_consistency_classifier

pytestmark = pytest.mark.integration

_REPO_ROOT = Path(__file__).resolve().parents[1]
CATEGORY_FILE = _REPO_ROOT / "categories.md"
SAMPLES_DIR = _REPO_ROOT / "samples"

# The committed live-fire corpus: sample file -> intended category label.
EXPECTED_CLASSIFICATIONS = {
    "Activity Log Report (Detective)_2020_Redacted.pdf": "Activity Logs",
    "Activity Log.pdf": "Activity Logs",
    "Arraignment Card_2022_Redacted.pdf": "Arraignment Card",
    "BWC Checklist-PD-220-141_2022_Redacted.pdf": "BWC Checklist",
    "MOS History Report_Summary of Officer CCRB History_Redacted Without Protective Order.pdf": "CCRB History Report",
    "NYPD Online Booking System Arrest Worksheet _ Redacted.pdf": "Arrest Report",
    "NYPD-BWC Metadata_2021_Redacted.pdf": "BWC Metadata",
    "NYPD-Omniform System Complaints_2022_Redacted.pdf": "Complaint Report",
    "NYPD-Omniform System-Arrests_2022_Redacted.pdf": "Arrest Report",
    "SEARCH WARRANT-NYPD_2021_Redacted.pdf": "Search Warrant",
}


@pytest.fixture(scope="module")
def voter() -> SelfConsistencyClassifier:
    """Build the real self-consistency classifier, or skip if no provider is configured."""
    settings = get_settings()
    provider_configured = (settings.provider == "anthropic" and settings.anthropic is not None) or (
        settings.provider == "foundry" and settings.foundry is not None
    )
    if not provider_configured:
        pytest.skip(f"live-fire test needs the {settings.provider!r} provider configured (real API call)")
    categories = parse_category_file(CATEGORY_FILE)
    return create_self_consistency_classifier(categories, settings)


@pytest.mark.parametrize(
    ("filename", "expected_category"),
    [pytest.param(name, category, id=name) for name, category in EXPECTED_CLASSIFICATIONS.items()],
)
def test_sample_classifies_to_expected_category(
    voter: SelfConsistencyClassifier, filename: str, expected_category: str
) -> None:
    verdict = voter.classify(extract_text(SAMPLES_DIR / filename))
    assert verdict.category == expected_category
