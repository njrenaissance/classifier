"""The committed category file must always parse.

`categories.md` is baked into the container image (E7) and parsed at runtime by
the processor. A malformed edit would otherwise only surface when the job runs in
production, so this test guards the real, committed file at CI time. Beyond
"it parses," these tests pin the E9/#42 acceptance criteria against the committed
production label set: the exact category names, that every category carries at
least one few-shot example, and that ``unknown`` is the single reserved bucket in
the enum (never a real category).
"""

from pathlib import Path

import pytest

from categories import UNKNOWN_CATEGORY, parse_category_file

pytestmark = pytest.mark.unit

CATEGORY_FILE = Path(__file__).resolve().parents[1] / "categories.md"

# The production (TEST BATCH) label set authored in E9 (#42), in file order.
EXPECTED_CATEGORIES = (
    "Arrest Report",
    "Complaint Report",
    "Arraignment Card",
    "BWC Checklist",
    "Activity Logs",
    "ECMS Access Log",
    "BWC Metadata",
    "Command Log",
    "CCRB History Report",
    "Search Warrant",
)


def test_committed_category_file_parses() -> None:
    result = parse_category_file(CATEGORY_FILE)

    assert result.names, "category file defines no real categories"
    assert UNKNOWN_CATEGORY in result.enum_values


def test_committed_categories_match_expected_set() -> None:
    result = parse_category_file(CATEGORY_FILE)

    assert result.names == EXPECTED_CATEGORIES


def test_every_committed_category_has_at_least_one_example() -> None:
    result = parse_category_file(CATEGORY_FILE)

    for category in result.categories:
        assert category.examples, f"category {category.name!r} has no examples"


def test_unknown_is_the_single_reserved_enum_value() -> None:
    result = parse_category_file(CATEGORY_FILE)

    assert result.enum_values == (*EXPECTED_CATEGORIES, UNKNOWN_CATEGORY)
    assert result.enum_values.count(UNKNOWN_CATEGORY) == 1
    real_names_lowered = {name.lower() for name in result.names}
    assert UNKNOWN_CATEGORY not in real_names_lowered
    assert "other" not in real_names_lowered
