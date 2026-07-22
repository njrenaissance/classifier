"""Command-line entry point (C1).

The thin CLI wrapper that strings the classifier's components into one local
end-to-end run (ADR-0003): point it at a local source path plus a category
Markdown file and it extracts, classifies (self-consistency), and writes a CSV.

The orchestration core (:func:`classify_documents`) is an importable function so
a library or service can be added later without a rewrite; :func:`run` is the
system boundary that wires configuration + components, catches domain failures
exactly once, and converts them into an exit code.
"""

import argparse
import logging
import sys
from collections.abc import Callable
from pathlib import Path

from pydantic import ValidationError

from categories import parse_category_file
from config import get_settings
from errors import AppError, ClassificationError, ExtractionError
from extraction import extract_text
from self_consistency import SelfConsistencyClassifier, create_self_consistency_classifier
from sources import DocumentSource, LocalFileSystemSource
from writer import ClassificationResult, write_results_csv

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="classifier",
        description="Classify local documents into categories and write a CSV.",
    )
    parser.add_argument("source", type=Path, help="Local file or directory of documents to classify.")
    parser.add_argument(
        "-c",
        "--categories",
        type=Path,
        required=True,
        help="Markdown file defining the categories + few-shot examples.",
    )
    parser.add_argument("-o", "--output", type=Path, required=True, help="Path to write the results CSV.")
    return parser


def relative_name(path: Path, root: Path) -> str:
    """Return ``path`` as a source-root-relative name for the CSV ``filename`` column.

    A directory root yields the nested relative path (``sub/a.pdf``); a single
    file root yields the bare basename. Relative names stay unique within a run,
    avoiding basename collisions across sub-directories.
    """
    base = root if root.is_dir() else root.parent
    return str(path.relative_to(base))


def classify_documents(
    source: DocumentSource,
    voter: SelfConsistencyClassifier,
    root: Path,
    extract: Callable[[Path], str] = extract_text,
) -> list[ClassificationResult]:
    """Classify every document from ``source`` into a list of CSV-ready results.

    Each document is extracted then classified N times (self-consistency). A file
    whose extraction or classification fails is skipped with a ``WARNING`` and the
    run continues, so one bad document does not abort a batch. Enumeration
    failures (:class:`~errors.SourceError`) are not caught here — they propagate to
    the caller and fail the run.
    """
    results: list[ClassificationResult] = []
    for path in source.documents():
        try:
            verdict = voter.classify(extract(path))
        except (ExtractionError, ClassificationError):
            logger.warning("Skipping %s after extraction/classification failure", path, exc_info=True)
            continue
        results.append(ClassificationResult(relative_name(path, root), verdict.category, verdict.confidence))
    return results


def configure_logging() -> None:
    """Configure stdlib logging once, at the application entry point."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def run(argv: list[str]) -> int:
    """Parse ``argv``, run the classification pipeline, and return an exit code."""
    args = build_parser().parse_args(argv)
    try:
        settings = get_settings()
        categories = parse_category_file(args.categories)
        voter = create_self_consistency_classifier(categories, settings)
        source = LocalFileSystemSource(args.source)
        results = classify_documents(source, voter, args.source)
        write_results_csv(results, args.output)
    except (AppError, ValidationError):
        # System boundary: convert any domain failure — or a missing/invalid
        # setting such as ANTHROPIC_API_KEY — into a clean, logged exit code.
        logger.exception("Classification run failed")
        return 1
    logger.info("Classified %d document(s); wrote %s", len(results), args.output)
    return 0


def main() -> None:
    """CLI entry point: configure logging, run, and exit with the status code."""
    configure_logging()
    raise SystemExit(run(sys.argv[1:]))


if __name__ == "__main__":
    main()
