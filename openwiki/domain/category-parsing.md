---
type: Domain Concept
title: Category Parsing
description: Markdown format for category definitions and the CategorySet parser
resource: /src/categories.py
tags: [category, parsing, markdown, domain]
---

# Category Parsing (A1)

Category definitions drive the entire classification system. They are read from a **Markdown file** (never hardcoded in code) and parsed into a `CategorySet` at runtime.

## Category Markdown Format

The category file uses a simple structure:

```markdown
# Document Classification Categories

Optional document title; ignored by parser.

## Invoice
Invoices, billing statements, receipts, and financial documents.

- Account statement dated 2024
- Monthly billing document
- Invoice from vendor
- Receipt for services rendered

## Contract
Legal agreements, contract templates, and formal documents.

- Service agreement
- Non-disclosure agreement
- Employment contract
- Licensing agreement

## Report
Business and technical reports, analyses, and documentation.

- Quarterly business report
- Technical analysis document
- Project status report
```

### Format Rules

- **H1 heading** (`#`) — Document title; optional and ignored
- **H2 heading** (`## Name`) — Defines a category; order is preserved
- **Prose before bullets** — Optional category description
- **Bullets** (`-`, `*`, or `+`) — Few-shot examples for the category

### Example Breakdown

For the "Invoice" category above:

- **Name:** `Invoice`
- **Description:** "Invoices, billing statements, receipts, and financial documents."
- **Examples:** Tuple of 4 strings:
  - "Account statement dated 2024"
  - "Monthly billing document"
  - "Invoice from vendor"
  - "Receipt for services rendered"

### Special Rules

- **Case-insensitive deduplication:** "Invoice" and "INVOICE" are treated as duplicates; the parser rejects them with `CategoryFileError`
- **Reserved `unknown` category:** If you define a category named `unknown` (case-insensitive), it is silently ignored — `unknown` is always a built-in bucket
- **No duplicate categories:** Duplicate H2 headings raise `CategoryFileError`
- **No empty category sets:** A file with no H2 headings raises `CategoryFileError`

## Parser Implementation

### Entry Points

```python
from categories import parse_category_file, parse_categories

# From file
categories = parse_category_file(Path("categories.md"))

# From string
markdown_text = """
## Invoice
- invoice example
"""
categories = parse_categories(markdown_text)
```

### Return Type: CategorySet

A frozen dataclass containing the parsed categories:

```python
@dataclass(frozen=True)
class CategorySet:
    categories: tuple[Category, ...]  # Ordered real categories
    
    @property
    def names(self) -> tuple[str, ...]:
        """Real category names, in file order (excludes 'unknown')."""
        return ('Invoice', 'Contract', 'Report')
    
    @property
    def enum_values(self) -> tuple[str, ...]:
        """Enum for structured output: real names + 'unknown'."""
        return ('Invoice', 'Contract', 'Report', 'unknown')
```

Each `Category` is also frozen:

```python
@dataclass(frozen=True)
class Category:
    name: str                    # e.g., "Invoice"
    description: str             # e.g., "Invoices, billing..."
    examples: tuple[str, ...]    # e.g., ("Account statement...", "Monthly...")
```

## Usage in the Classifier

The `CategorySet` is used by the `Classifier` to:

1. **Render the static system prompt** — Includes all categories, descriptions, and examples
2. **Build the structured-output enum** — The JSON schema constrains the model to return only defined labels + `unknown`
3. **Validate runtime results** — Ensure returned labels are in the enum

Example prompt rendering (from `src/classifier.py`):

```
You are a document classifier. Classify the document into exactly one of the categories below.

# Categories

## Invoice
Invoices, billing statements, receipts, and financial documents.
Examples:
- Account statement dated 2024
- Monthly billing document
- Invoice from vendor
- Receipt for services rendered

## Contract
Legal agreements, contract templates, and formal documents.
Examples:
- Service agreement
- Non-disclosure agreement
- Employment contract
- Licensing agreement

## Report
Business and technical reports, analyses, and documentation.
Examples:
- Quarterly business report
- Technical analysis document
- Project status report

If the document does not clearly fit any category, respond with 'unknown'.
Respond with exactly one category label.
```

## Error Handling

All parser errors raise `CategoryFileError`:

```python
from errors import CategoryFileError

try:
    categories = parse_category_file(Path("categories.md"))
except CategoryFileError as e:
    # Handle: file missing, malformed, duplicates, etc.
    print(f"Category parsing failed: {e}")
```

## Integration Points

- **CLI** — Loads the category file from the user-provided path
- **Classifier** — Uses `CategorySet` to build the prompt and schema
- **SelfConsistencyClassifier** — Inherits the `CategorySet` from the inner `Classifier`
- **Tests** — Use mock `CategorySet` objects to avoid I/O

## Design Rationale

**Why Markdown?**
- Human-readable for users defining categories
- Few-shot examples are natural as bulleted lists
- Descriptions are plain prose, not JSON schemas

**Why parse at runtime?**
- Categories are never hardcoded; they are part of the data, not the code
- Different runs can use different category files

**Why frozen dataclasses?**
- Immutable, hashable, thread-safe (not currently used, but safe for future concurrency)
- Clear intent: categories don't change after parsing

**Why deduplicate case-insensitively?**
- Prevents subtle bugs where "Invoice" and "INVOICE" are treated as different labels
- Keeps output unambiguous and deterministic

See [../architecture/overview.md](../architecture/overview.md#a1-category-parsing) for the role of A1 in the system and [../workflows/classification-pipeline.md](../workflows/classification-pipeline.md#step-1-parse-categories-a1) for the pipeline context.
