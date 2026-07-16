import logging
from pathlib import Path

import pytest

from errors import AppError, SourceError
from sources import LocalFileSystemSource

pytestmark = pytest.mark.unit


def _touch(path: Path) -> Path:
    """Create an empty file (enumeration inspects the suffix, not the content)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    return path


def _documents(root: Path) -> list[Path]:
    return list(LocalFileSystemSource(root).documents())


def test_directory_yields_only_supported_files_each_once(tmp_path: Path):
    pdf = _touch(tmp_path / "invoice.pdf")
    docx = _touch(tmp_path / "contract.docx")
    _touch(tmp_path / "notes.txt")
    _touch(tmp_path / "legacy.doc")

    result = _documents(tmp_path)

    assert result == sorted([pdf, docx])
    assert len(result) == len(set(result))


def test_directory_walk_is_recursive(tmp_path: Path):
    top = _touch(tmp_path / "top.pdf")
    nested = _touch(tmp_path / "sub" / "deep" / "nested.docx")

    assert _documents(tmp_path) == sorted([top, nested])


def test_symlinked_subdirectory_is_not_traversed(tmp_path: Path):
    outside = tmp_path / "outside"
    _touch(outside / "target.pdf")
    root = tmp_path / "root"
    root.mkdir()
    kept = _touch(root / "kept.pdf")
    (root / "linked").symlink_to(outside, target_is_directory=True)

    # The document behind the symlinked directory is not enumerated (loop-safe walk).
    assert _documents(root) == [kept]


def test_symlink_to_supported_file_is_enumerated(tmp_path: Path):
    real = _touch(tmp_path / "real.pdf")
    root = tmp_path / "root"
    root.mkdir()
    alias = root / "alias.pdf"
    alias.symlink_to(real)

    assert _documents(root) == [alias]


@pytest.mark.parametrize(
    "name",
    [
        pytest.param("Report.PDF", id="upper_pdf"),
        pytest.param("Memo.DOCX", id="upper_docx"),
        pytest.param("Mixed.PdF", id="mixed_pdf"),
    ],
)
def test_suffix_match_is_case_insensitive(tmp_path: Path, name: str):
    path = _touch(tmp_path / name)
    assert _documents(tmp_path) == [path]


def test_result_is_deterministically_sorted(tmp_path: Path):
    created = [_touch(tmp_path / name) for name in ("c.pdf", "a.docx", "b.pdf")]
    assert _documents(tmp_path) == sorted(created)


def test_single_supported_file_yields_itself(tmp_path: Path):
    pdf = _touch(tmp_path / "only.pdf")
    assert _documents(pdf) == [pdf]


def test_single_unsupported_file_yields_nothing_and_warns(tmp_path: Path, caplog):
    unsupported = _touch(tmp_path / "legacy.doc")

    with caplog.at_level(logging.WARNING):
        result = _documents(unsupported)

    assert result == []
    assert any("legacy.doc" in message for message in caplog.messages)


def test_unsupported_files_in_directory_are_skipped_with_warning(tmp_path: Path, caplog):
    pdf = _touch(tmp_path / "keep.pdf")
    _touch(tmp_path / "notes.txt")
    _touch(tmp_path / "legacy.doc")

    with caplog.at_level(logging.WARNING):
        result = _documents(tmp_path)

    assert result == [pdf]
    warned = " ".join(caplog.messages)
    assert "notes.txt" in warned
    assert "legacy.doc" in warned


def test_empty_directory_yields_nothing_without_error(tmp_path: Path):
    assert _documents(tmp_path) == []


def test_missing_path_raises_source_error(tmp_path: Path):
    with pytest.raises(SourceError):
        _documents(tmp_path / "does-not-exist")


def test_source_error_is_an_app_error(tmp_path: Path):
    with pytest.raises(AppError):
        _documents(tmp_path / "does-not-exist")
