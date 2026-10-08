"""Live-fire baseline: classify the committed sample corpus against the real API.

This promotes issue #42's "a representative document per category classifies to the
intended label" acceptance criterion into a repeatable, committed test over the
`samples/` corpus. It makes **real** inference calls (one call per document) through the
configured inference provider (Jev, ADR-0022), so it is marked ``integration`` and
**skips** unless Jev is configured — CI (no credentials) and the ``-m unit`` pre-commit run never touch
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
from classifier import LabelClassifier, create_classifier
from config import get_settings
from extraction import extract_text

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
def classifier() -> LabelClassifier:
    """Build the real Jev classifier, or skip if Jev is not configured."""
    settings = get_settings()
    if settings.jev is None or not settings.jev.is_configured:
        pytest.skip("live-fire test needs Jev configured (real API call)")
    categories = parse_category_file(CATEGORY_FILE)
    return create_classifier(categories, settings)


@pytest.mark.parametrize(
    ("filename", "expected_category"),
    [pytest.param(name, category, id=name) for name, category in EXPECTED_CLASSIFICATIONS.items()],
)
def test_sample_classifies_to_expected_category(
    classifier: LabelClassifier, filename: str, expected_category: str
) -> None:
    classification = classifier.classify(extract_text(SAMPLES_DIR / filename))
    assert classification.category == expected_category
