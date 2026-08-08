"""The committed category file must always parse.

`categories.md` is baked into the container image (E7) and parsed at runtime by
the processor. A malformed edit would otherwise only surface when the job runs in
production, so this test guards the real, committed file at CI time — including
when a future change (E9/#42) replaces the placeholder content.
"""

from pathlib import Path

import pytest

from categories import parse_category_file

pytestmark = pytest.mark.unit

CATEGORY_FILE = Path(__file__).resolve().parents[1] / "categories.md"


def test_committed_category_file_parses() -> None:
    result = parse_category_file(CATEGORY_FILE)

    assert result.names, "category file defines no real categories"
    assert "unknown" in result.enum_values
