import pytest

from categories import CategorySet, parse_categories, parse_category_file
from errors import CategoryFileError

WELL_FORMED = """\
# Categories

## Invoice
Billing documents requesting payment.

- Invoice #4521, total due $1,200, net 30
- Remittance advice for account 8891

## Contract
Legally binding agreements between parties.

- Master Services Agreement between Acme and Globex
- This Agreement is entered into as of January 1
"""


def test_parses_categories_with_descriptions_and_examples():
    result = parse_categories(WELL_FORMED)
    assert isinstance(result, CategorySet)
    assert result.names == ("Invoice", "Contract")
    invoice = result.categories[0]
    assert invoice.description == "Billing documents requesting payment."
    assert invoice.examples == (
        "Invoice #4521, total due $1,200, net 30",
        "Remittance advice for account 8891",
    )


def test_categories_preserve_file_order():
    assert parse_categories(WELL_FORMED).names == ("Invoice", "Contract")


def test_enum_appends_unknown_last():
    assert parse_categories(WELL_FORMED).enum_values == ("Invoice", "Contract", "unknown")


def test_unknown_in_file_is_not_duplicated():
    text = "## Invoice\n- inv example\n\n## unknown\n- something odd\n\n## Contract\n- contract example\n"
    result = parse_categories(text)
    assert result.names == ("Invoice", "Contract")
    assert result.enum_values == ("Invoice", "Contract", "unknown")
    assert result.enum_values.count("unknown") == 1


def test_parse_category_file_reads_from_disk(tmp_path):
    path = tmp_path / "categories.md"
    path.write_text(WELL_FORMED)
    assert parse_category_file(path).names == ("Invoice", "Contract")


def test_missing_file_errors(tmp_path):
    with pytest.raises(CategoryFileError):
        parse_category_file(tmp_path / "does_not_exist.md")


@pytest.mark.parametrize(
    "markdown",
    [
        pytest.param("", id="empty"),
        pytest.param("# Categories\n\nJust prose, no headings.\n", id="no_categories"),
        pytest.param("## Invoice\n- a\n\n## Invoice\n- b\n", id="duplicate_names"),
        pytest.param("## Invoice\nNo examples here.\n", id="category_without_examples"),
    ],
)
def test_malformed_markdown_errors(markdown):
    with pytest.raises(CategoryFileError):
        parse_categories(markdown)
