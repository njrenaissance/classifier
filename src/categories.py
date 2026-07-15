"""Category-file parser (A1).

Parses the config-controlled Markdown category file into an ordered set of
categories, each with its few-shot examples, and exposes the runtime enum
(the defined categories plus the reserved ``unknown`` bucket) used to build
the classifier's structured-output schema (ADR-0001 / ADR-0008).

Format:
    - ``#``  H1 heading  — optional document title, ignored.
    - ``## Name``        — a category; ordered by appearance in the file.
    - prose before the first bullet — the category's description (optional).
    - ``-``/``*`` bullets — the category's few-shot examples.

The parser is intentionally decoupled from application settings: it takes
Markdown text or a path, never reads configuration itself. Every failure is
raised as :class:`~errors.CategoryFileError`.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from errors import CategoryFileError

UNKNOWN_CATEGORY = "unknown"

_H2_HEADING = re.compile(r"^##\s+(.+?)\s*$")
_BULLET = re.compile(r"^\s*[-*]\s+(.+?)\s*$")


@dataclass(frozen=True)
class Category:
    """A single defined category and its few-shot examples."""

    name: str
    description: str
    examples: tuple[str, ...]


@dataclass(frozen=True)
class CategorySet:
    """The ordered set of real categories parsed from the category file."""

    categories: tuple[Category, ...]

    @property
    def names(self) -> tuple[str, ...]:
        """The real category names, in file order (excludes ``unknown``)."""
        return tuple(category.name for category in self.categories)

    @property
    def enum_values(self) -> tuple[str, ...]:
        """The structured-output enum: real names then a single ``unknown``."""
        return (*self.names, UNKNOWN_CATEGORY)


def parse_category_file(path: Path) -> CategorySet:
    """Read and parse the category Markdown file at ``path``."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as err:
        raise CategoryFileError(f"Cannot read category file: {path}") from err
    return parse_categories(text)


def parse_categories(markdown: str) -> CategorySet:
    """Parse category-definition Markdown into a :class:`CategorySet`."""
    categories: list[Category] = []
    seen: set[str] = set()
    for name, body in _split_into_blocks(markdown):
        if name.lower() == UNKNOWN_CATEGORY:
            continue  # reserved bucket — deduped into enum_values, never a real category
        if name.lower() in seen:
            raise CategoryFileError(f"Duplicate category: {name!r}")
        seen.add(name.lower())
        categories.append(_build_category(name, body))
    if not categories:
        raise CategoryFileError("Category file defines no categories (expected '## Name' headings).")
    return CategorySet(categories=tuple(categories))


def _split_into_blocks(markdown: str) -> list[tuple[str, list[str]]]:
    """Group lines into (category name, body lines); ignore any preamble."""
    blocks: list[tuple[str, list[str]]] = []
    for line in markdown.splitlines():
        heading = _H2_HEADING.match(line)
        if heading:
            blocks.append((heading.group(1), []))
        elif blocks:
            blocks[-1][1].append(line)
    return blocks


def _build_category(name: str, body: list[str]) -> Category:
    """Parse one category's body into a description and its examples."""
    description_parts: list[str] = []
    examples: list[str] = []
    for line in body:
        bullet = _BULLET.match(line)
        if bullet:
            examples.append(bullet.group(1))
        elif not examples and line.strip():
            description_parts.append(line.strip())
    if not examples:
        raise CategoryFileError(f"Category {name!r} has no few-shot examples.")
    return Category(name=name, description=" ".join(description_parts), examples=tuple(examples))
