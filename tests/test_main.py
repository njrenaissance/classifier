from pathlib import Path
from types import SimpleNamespace

import pytest
from docx import Document
from pydantic import ValidationError

import main
from errors import CategoryFileError, ClassificationError, ExtractionError
from self_consistency import Verdict
from writer import ClassificationResult

pytestmark = pytest.mark.unit


def _voter(*verdicts: Verdict):
    """A stand-in voter that returns the scripted verdicts, one per ``classify`` call."""
    scripted = iter(verdicts)
    return SimpleNamespace(classify=lambda _text: next(scripted))


def _source(*paths: Path):
    """A stand-in :class:`DocumentSource` yielding the given paths."""
    return SimpleNamespace(documents=lambda: list(paths))


@pytest.mark.parametrize(
    ("relative", "as_dir_root"),
    [
        pytest.param("a.pdf", True, id="dir_root_top_level"),
        pytest.param("sub/a.pdf", True, id="dir_root_nested"),
        pytest.param("a.pdf", False, id="file_root_basename"),
    ],
)
def test_relative_name(tmp_path, relative, as_dir_root):
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x")
    root = tmp_path if as_dir_root else path
    assert main.relative_name(path, root) == relative


def test_classify_documents_one_result_per_document(tmp_path):
    first, second = tmp_path / "a.pdf", tmp_path / "sub" / "b.docx"
    voter = _voter(Verdict("Invoice", 1.0), Verdict("unknown", 0.4))

    results = main.classify_documents(_source(first, second), voter, tmp_path, extract=lambda _p: "text")

    assert results == [
        ClassificationResult("a.pdf", "Invoice", 1.0),
        ClassificationResult("sub/b.docx", "unknown", 0.4),
    ]


def test_classify_documents_skips_failed_files(tmp_path, caplog):
    good, bad_extract, bad_classify = (tmp_path / n for n in ("good.pdf", "extract.pdf", "classify.pdf"))

    def extract(path: Path) -> str:
        if path == bad_extract:
            raise ExtractionError("boom")
        return "text"

    def classify(_text: str) -> Verdict:
        if classify.calls == 1:  # second surviving document raises
            raise ClassificationError("boom")
        classify.calls += 1
        return Verdict("Invoice", 1.0)

    classify.calls = 0
    voter = SimpleNamespace(classify=classify)

    with caplog.at_level("WARNING"):
        results = main.classify_documents(_source(good, bad_extract, bad_classify), voter, tmp_path, extract=extract)

    assert results == [ClassificationResult("good.pdf", "Invoice", 1.0)]
    assert "extract.pdf" in caplog.text
    assert "classify.pdf" in caplog.text


def test_build_parser_parses_arguments():
    args = main.build_parser().parse_args(["docs", "-c", "cats.md", "-o", "out.csv"])
    assert (args.source, args.categories, args.output) == (Path("docs"), Path("cats.md"), Path("out.csv"))


@pytest.mark.parametrize(
    "argv",
    [
        pytest.param(["docs", "-o", "out.csv"], id="missing_categories"),
        pytest.param(["docs", "-c", "cats.md"], id="missing_output"),
        pytest.param(["-c", "cats.md", "-o", "out.csv"], id="missing_source"),
    ],
)
def test_build_parser_requires_all_arguments(argv):
    with pytest.raises(SystemExit):
        main.build_parser().parse_args(argv)


def test_run_wires_pipeline_and_returns_zero(mocker, tmp_path):
    results = [ClassificationResult("a.pdf", "Invoice", 1.0)]
    settings = mocker.patch("main.get_settings").return_value
    categories = mocker.patch("main.parse_category_file").return_value
    voter = mocker.patch("main.create_self_consistency_classifier").return_value
    source = mocker.patch("main.LocalFileSystemSource").return_value
    classify = mocker.patch("main.classify_documents", return_value=results)
    write = mocker.patch("main.write_results_csv")

    exit_code = main.run([str(tmp_path), "-c", "cats.md", "-o", "out.csv"])

    assert exit_code == 0
    main.parse_category_file.assert_called_once_with(Path("cats.md"))
    main.create_self_consistency_classifier.assert_called_once_with(categories, settings)
    main.LocalFileSystemSource.assert_called_once_with(tmp_path)
    classify.assert_called_once_with(source, voter, tmp_path)
    write.assert_called_once_with(results, Path("out.csv"))


def test_run_returns_one_on_app_error(mocker, caplog):
    mocker.patch("main.get_settings")
    mocker.patch("main.parse_category_file", side_effect=CategoryFileError("bad file"))

    with caplog.at_level("ERROR"):
        exit_code = main.run(["docs", "-c", "cats.md", "-o", "out.csv"])

    assert exit_code == 1
    assert "Classification run failed" in caplog.text


def test_run_returns_one_on_missing_settings(mocker, caplog):
    error = ValidationError.from_exception_data("Settings", [])
    mocker.patch("main.get_settings", side_effect=error)

    with caplog.at_level("ERROR"):
        exit_code = main.run(["docs", "-c", "cats.md", "-o", "out.csv"])

    assert exit_code == 1
    assert "Classification run failed" in caplog.text


@pytest.mark.integration
def test_end_to_end_local_run(mocker, monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    categories = tmp_path / "categories.md"
    categories.write_text("## Invoice\nBilling documents.\n\n- Invoice #1 total due $10\n")
    docs = tmp_path / "docs"
    docs.mkdir()
    document = Document()
    document.add_paragraph("Invoice #4521, total due $1,200")
    document.save(str(docs / "bill.docx"))
    output = tmp_path / "out.csv"
    mocker.patch(
        "main.create_self_consistency_classifier",
        return_value=_voter(Verdict("Invoice", 0.8)),
    )

    exit_code = main.run([str(docs), "-c", str(categories), "-o", str(output)])

    assert exit_code == 0
    assert output.read_text().splitlines() == ["filename,category,confidence", "bill.docx,Invoice,0.80"]
