import csv
from pathlib import Path

import pytest

from errors import AppError, OutputError
from writer import ClassificationResult, write_results_csv

pytestmark = pytest.mark.unit


def _read_rows(path: Path) -> list[list[str]]:
    """Parse a written CSV back into a list of rows (header included)."""
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.reader(handle))


def test_headers_are_the_three_columns_in_order():
    assert ClassificationResult.headers() == ("filename", "category", "confidence")


def test_row_renders_confidence_to_two_decimals():
    result = ClassificationResult("doc.pdf", "invoice", 0.6)
    assert result.row() == ("doc.pdf", "invoice", "0.60")


def test_writes_header_then_rows_with_three_columns(tmp_path: Path):
    out = tmp_path / "results.csv"

    write_results_csv(
        [
            ClassificationResult("invoice.pdf", "invoice", 0.6),
            ClassificationResult("memo.docx", "correspondence", 1.0),
        ],
        out,
    )

    rows = _read_rows(out)
    assert rows[0] == list(ClassificationResult.headers())
    assert rows[0] == ["filename", "category", "confidence"]
    assert rows[1:] == [
        ["invoice.pdf", "invoice", "0.60"],
        ["memo.docx", "correspondence", "1.00"],
    ]


def test_rows_are_written_in_the_order_given(tmp_path: Path):
    out = tmp_path / "results.csv"
    names = ["c.pdf", "a.pdf", "b.pdf"]

    write_results_csv([ClassificationResult(name, "invoice", 0.8) for name in names], out)

    assert [row[0] for row in _read_rows(out)[1:]] == names


@pytest.mark.parametrize(
    ("confidence", "expected"),
    [
        pytest.param(0.6, "0.60", id="one_decimal"),
        pytest.param(1.0, "1.00", id="whole"),
        pytest.param(0.0, "0.00", id="zero"),
        pytest.param(2 / 3, "0.67", id="rounds_up"),
        pytest.param(0.833, "0.83", id="rounds_down"),
    ],
)
def test_confidence_is_formatted_to_two_decimals(tmp_path: Path, confidence: float, expected: str):
    out = tmp_path / "results.csv"

    write_results_csv([ClassificationResult("doc.pdf", "invoice", confidence)], out)

    assert _read_rows(out)[1][2] == expected


def test_overwrites_existing_file(tmp_path: Path):
    out = tmp_path / "results.csv"
    write_results_csv([ClassificationResult("old.pdf", "invoice", 0.4)], out)

    write_results_csv([ClassificationResult("new.pdf", "receipt", 0.9)], out)

    rows = _read_rows(out)
    assert rows[1:] == [["new.pdf", "receipt", "0.90"]]


def test_creates_missing_parent_directories(tmp_path: Path):
    out = tmp_path / "nested" / "deep" / "results.csv"

    write_results_csv([ClassificationResult("doc.pdf", "invoice", 0.5)], out)

    assert out.exists()
    assert _read_rows(out)[1:] == [["doc.pdf", "invoice", "0.50"]]


def test_empty_results_writes_header_only(tmp_path: Path):
    out = tmp_path / "results.csv"

    write_results_csv([], out)

    assert _read_rows(out) == [list(ClassificationResult.headers())]


def test_accepts_a_lazy_iterator(tmp_path: Path):
    out = tmp_path / "results.csv"
    results = (ClassificationResult(f"doc{i}.pdf", "invoice", 0.5) for i in range(3))

    write_results_csv(results, out)

    assert [row[0] for row in _read_rows(out)[1:]] == ["doc0.pdf", "doc1.pdf", "doc2.pdf"]


@pytest.mark.parametrize(
    ("filename", "category"),
    [
        pytest.param("a,b.pdf", "in,voice", id="comma"),
        pytest.param('quote".pdf', 'ca"tegory', id="double_quote"),
        pytest.param("line\nbreak.pdf", "multi\nline", id="newline"),
    ],
)
def test_special_characters_survive_round_trip(tmp_path: Path, filename: str, category: str):
    out = tmp_path / "results.csv"

    write_results_csv([ClassificationResult(filename, category, 0.7)], out)

    assert _read_rows(out)[1] == [filename, category, "0.70"]


def test_unknown_category_is_written_like_any_other(tmp_path: Path):
    out = tmp_path / "results.csv"

    write_results_csv([ClassificationResult("mystery.pdf", "unknown", 0.2)], out)

    assert _read_rows(out)[1] == ["mystery.pdf", "unknown", "0.20"]


def test_unwritable_path_raises_output_error(tmp_path: Path):
    # A path whose target is an existing directory cannot be opened for writing.
    target_is_a_dir = tmp_path / "results.csv"
    target_is_a_dir.mkdir()

    with pytest.raises(OutputError) as excinfo:
        write_results_csv([ClassificationResult("doc.pdf", "invoice", 0.5)], target_is_a_dir)

    assert isinstance(excinfo.value.__cause__, OSError)


def test_output_error_is_an_app_error(tmp_path: Path):
    target_is_a_dir = tmp_path / "results.csv"
    target_is_a_dir.mkdir()

    with pytest.raises(AppError):
        write_results_csv([ClassificationResult("doc.pdf", "invoice", 0.5)], target_is_a_dir)
