---
type: Testing Guide
title: Testing Strategy
description: Unit test patterns, mocking strategies, and test organization
resource: /tests
tags: [testing, unit-tests, mocking, pytest]
---

# Testing Strategy

The classifier uses **unit tests with mocked dependencies** to test business logic without hitting the Anthropic API or filesystem.

## Test Organization

Tests live in `/tests/` with one test module per source module:

```
tests/
├── conftest.py              # Shared fixtures
├── test_categories.py       # Category parser tests (A1)
├── test_extraction.py       # Text extraction tests (A2)
├── test_sources.py          # Document source tests (A3)
├── test_classifier.py       # Classifier core tests (B1)
├── test_self_consistency.py # Self-consistency tests (B2)
├── test_writer.py           # CSV writer tests (A4)
├── test_config.py           # Configuration tests
└── test_main.py             # CLI integration tests (TBD)
```

## Running Tests

```bash
# Run all tests
uv run pytest

# Run with coverage
uv run pytest --cov=src

# Run a single test file
uv run pytest tests/test_classifier.py

# Run a single test
uv run pytest tests/test_classifier.py::test_classify_success

# Run with verbose output
uv run pytest -v

# Run only unit tests (exclude integration tests)
uv run pytest -m unit
```

## Test Coverage

Current target: **70% line coverage** (enforced by `pytest-cov` in `pyproject.toml`).

```toml
[tool.coverage.run]
source = ["src"]
omit = ["tests/*"]

[tool.coverage.report]
fail_under = 70
show_missing = true
```

## Key Testing Patterns

### Unit Tests: Category Parsing (A1)

**Goal:** Test the Markdown parser in isolation.

```python
# tests/test_categories.py

from pathlib import Path
from categories import parse_categories, parse_category_file, CategoryFileError
import pytest

def test_parse_single_category():
    markdown = """
## Invoice
Invoice documents

- Invoice example
"""
    categories = parse_categories(markdown)
    assert len(categories.categories) == 1
    assert categories.categories[0].name == "Invoice"
    assert categories.enum_values == ("Invoice", "unknown")

def test_duplicate_categories_rejected():
    markdown = """
## Invoice
- example

## Invoice
- duplicate
"""
    with pytest.raises(CategoryFileError, match="Duplicate category"):
        parse_categories(markdown)

def test_no_categories_rejected():
    markdown = "No headings"
    with pytest.raises(CategoryFileError, match="no categories"):
        parse_categories(markdown)

def test_parse_from_file(tmp_path):
    # Create a temporary category file
    cat_file = tmp_path / "categories.md"
    cat_file.write_text("""
## Invoice
- example
""")
    categories = parse_category_file(cat_file)
    assert len(categories.categories) == 1
```

**Approach:** Direct unit tests on the parser; no mocking needed.

### Unit Tests: Text Extraction (A2)

**Goal:** Test extractors without hitting the filesystem (when possible).

```python
# tests/test_extraction.py

from pathlib import Path
from extraction import extract_text, PdfTextExtractor, DocxTextExtractor, \
    ExtractionError, UnsupportedFormatError
from unittest.mock import Mock, patch, MagicMock
import pytest

def test_extract_text_pdf(tmp_path):
    # Create a minimal valid PDF (or use a fixture)
    pdf_path = tmp_path / "test.pdf"
    # ... write PDF bytes ...
    
    text = extract_text(pdf_path)
    assert isinstance(text, str)

def test_unsupported_format():
    with pytest.raises(UnsupportedFormatError):
        extract_text(Path("document.txt"))

def test_extract_missing_file():
    with pytest.raises(ExtractionError, match="Cannot extract"):
        extract_text(Path("/nonexistent/file.pdf"))

def test_pdf_extractor_with_mocked_pypdf():
    # Mock the PdfReader to avoid I/O
    with patch("extraction.PdfReader") as mock_reader:
        mock_reader.return_value.pages = [
            Mock(extract_text=lambda: "Page 1"),
            Mock(extract_text=lambda: ""),  # Blank page
            Mock(extract_text=lambda: "Page 3"),
        ]
        
        extractor = PdfTextExtractor()
        text = extractor.extract(Path("any.pdf"))
        
        # Blank page is filtered out
        assert "Page 1" in text
        assert "Page 3" in text

def test_docx_extractor_with_mocked_docx():
    with patch("extraction.Document") as mock_doc:
        # Mock paragraphs
        mock_doc.return_value.paragraphs = [
            Mock(text="Paragraph 1"),
            Mock(text="Paragraph 2"),
        ]
        mock_doc.return_value.tables = []
        
        extractor = DocxTextExtractor()
        text = extractor.extract(Path("any.docx"))
        
        assert "Paragraph 1" in text
        assert "Paragraph 2" in text
```

**Approach:** Mock file readers to avoid I/O and test extraction logic.

### Unit Tests: Document Sources (A3)

**Goal:** Test enumeration without touching the filesystem (when possible).

```python
# tests/test_sources.py

from pathlib import Path
from sources import LocalFileSystemSource, DocumentSource
from errors import SourceError
from unittest.mock import patch, MagicMock
import pytest

def test_enumerate_single_file(tmp_path):
    # Create a real test file
    doc = tmp_path / "test.pdf"
    doc.touch()
    
    source = LocalFileSystemSource(doc)
    docs = source.documents()
    
    assert list(docs) == [doc]

def test_enumerate_directory_recursive(tmp_path):
    # Create a test directory structure
    (tmp_path / "sub").mkdir()
    (tmp_path / "test1.pdf").touch()
    (tmp_path / "sub" / "test2.pdf").touch()
    (tmp_path / "unsupported.txt").touch()
    
    source = LocalFileSystemSource(tmp_path)
    docs = source.documents()
    
    # Only .pdf files (mock supported_suffixes)
    with patch("sources.supported_suffixes", return_value={".pdf"}):
        docs = source.documents()
        assert len(docs) == 2  # test1.pdf and sub/test2.pdf

def test_invalid_source_path():
    with pytest.raises(SourceError, match="not a file or directory"):
        source = LocalFileSystemSource(Path("/nonexistent/"))
        source.documents()

def test_symlink_to_directory_rejected(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target)
    
    source = LocalFileSystemSource(link)
    # Should raise SourceError because symlink is not directly a file or dir
    with pytest.raises(SourceError):
        source.documents()
```

**Approach:** Use temporary directories (`tmp_path` fixture) for real filesystem tests; mock `supported_suffixes()` to control enumeration.

### Unit Tests: Classifier Core (B1)

**Goal:** Test the LLM call without hitting the Anthropic API.

```python
# tests/test_classifier.py

from classifier import Classifier, _render_category_block
from categories import CategorySet, Category
from errors import ClassificationError
from anthropic.types import Message, TextBlock
from unittest.mock import Mock, patch
import pytest

@pytest.fixture
def test_categories():
    return CategorySet(categories=(
        Category("Invoice", "Invoice docs", ("Invoice example",)),
        Category("Contract", "Contracts", ("Contract example",)),
    ))

@pytest.fixture
def mock_client():
    return Mock()

def test_render_category_block(test_categories):
    block = _render_category_block(test_categories)
    assert "## Invoice" in block
    assert "Invoice example" in block
    assert "If the document does not clearly fit" in block

def test_classify_success(test_categories, mock_client):
    # Mock the Anthropic API response
    mock_response = Mock(spec=Message)
    mock_response.content = [Mock(spec=TextBlock, text='{"category":"Invoice"}')]
    mock_client.messages.create.return_value = mock_response
    
    classifier = Classifier(test_categories, mock_client, temperature=0.4)
    label = classifier.classify("Invoice document text")
    
    assert label == "Invoice"
    mock_client.messages.create.assert_called_once()

def test_classify_unknown(test_categories, mock_client):
    mock_response = Mock(spec=Message)
    mock_response.content = [Mock(spec=TextBlock, text='{"category":"unknown"}')]
    mock_client.messages.create.return_value = mock_response
    
    classifier = Classifier(test_categories, mock_client, temperature=0.4)
    label = classifier.classify("Unclassifiable text")
    
    assert label == "unknown"

def test_invalid_response_format(test_categories, mock_client):
    # Mock an invalid response
    mock_response = Mock(spec=Message)
    mock_response.content = [Mock(spec=TextBlock, text="Invalid JSON")]
    mock_client.messages.create.return_value = mock_response
    
    classifier = Classifier(test_categories, mock_client, temperature=0.4)
    
    with pytest.raises(ClassificationError):
        classifier.classify("Document text")

def test_prompt_caching_optimization(test_categories, mock_client):
    # Verify the static block is cached
    mock_response = Mock(spec=Message)
    mock_response.content = [Mock(spec=TextBlock, text='{"category":"Invoice"}')]
    mock_client.messages.create.return_value = mock_response
    
    classifier = Classifier(test_categories, mock_client, temperature=0.4)
    
    # The static block is built once at construction
    block_1 = classifier._system_block
    
    # Classify two documents
    classifier.classify("Doc 1")
    classifier.classify("Doc 2")
    
    # The block is unchanged (same object)
    block_2 = classifier._system_block
    assert block_1 is block_2
    assert block_1 == block_2  # Content is identical
```

**Approach:** Mock the Anthropic client to return controlled responses; verify prompt caching by checking object identity.

### Unit Tests: Self-Consistency (B2)

**Goal:** Test the voting algorithm without hitting the API.

```python
# tests/test_self_consistency.py

from self_consistency import SelfConsistencyClassifier, Verdict
from classifier import Classifier
from categories import CategorySet, Category
from errors import UNKNOWN_CATEGORY
from unittest.mock import Mock
import pytest

@pytest.fixture
def test_categories():
    return CategorySet(categories=(
        Category("Invoice", "Invoices", ("Invoice",)),
        Category("Contract", "Contracts", ("Contract",)),
    ))

@pytest.fixture
def mock_classifier():
    return Mock(spec=Classifier)

def test_unanimous_vote(mock_classifier, test_categories):
    # All runs return the same label
    mock_classifier.classify.side_effect = [
        "Invoice", "Invoice", "Invoice", "Invoice", "Invoice"
    ]
    
    sc = SelfConsistencyClassifier(mock_classifier, n=5, confidence_threshold=0.6)
    verdict = sc.classify("Document text")
    
    assert verdict.category == "Invoice"
    assert verdict.confidence == 1.0

def test_strong_agreement(mock_classifier, test_categories):
    # 4/5 agree
    mock_classifier.classify.side_effect = [
        "Invoice", "Invoice", "Invoice", "Invoice", "Contract"
    ]
    
    sc = SelfConsistencyClassifier(mock_classifier, n=5, confidence_threshold=0.6)
    verdict = sc.classify("Document text")
    
    assert verdict.category == "Invoice"
    assert verdict.confidence == 0.8

def test_weak_agreement_above_threshold(mock_classifier, test_categories):
    # 3/5 agree, confidence = 0.6, threshold = 0.6 (at threshold)
    mock_classifier.classify.side_effect = [
        "Invoice", "Invoice", "Invoice", "Contract", "unknown"
    ]
    
    sc = SelfConsistencyClassifier(mock_classifier, n=5, confidence_threshold=0.6)
    verdict = sc.classify("Document text")
    
    # 0.6 <= 0.6 (threshold) → resolve to unknown
    assert verdict.category == UNKNOWN_CATEGORY
    assert verdict.confidence == 0.6

def test_tie_resolves_to_unknown(mock_classifier, test_categories):
    # 2/5 Invoice, 2/5 Contract, 1/5 unknown
    mock_classifier.classify.side_effect = [
        "Invoice", "Invoice", "Contract", "Contract", "unknown"
    ]
    
    sc = SelfConsistencyClassifier(mock_classifier, n=5, confidence_threshold=0.6)
    verdict = sc.classify("Document text")
    
    # Tie at the top → resolve to unknown
    assert verdict.category == UNKNOWN_CATEGORY

def test_error_propagation(mock_classifier, test_categories):
    # One run raises an error
    from errors import ClassificationError
    mock_classifier.classify.side_effect = [
        "Invoice", "Invoice", ClassificationError("API error"), ...
    ]
    
    sc = SelfConsistencyClassifier(mock_classifier, n=5, confidence_threshold=0.6)
    
    with pytest.raises(ClassificationError):
        sc.classify("Document text")
```

**Approach:** Mock the inner classifier to return controlled labels; verify voting logic.

### Unit Tests: CSV Writer (A4)

**Goal:** Test CSV output without complex setup.

```python
# tests/test_writer.py

from pathlib import Path
from writer import ClassificationResult, write_results_csv
from errors import OutputError
import csv
import pytest

def test_classification_result_headers():
    headers = ClassificationResult.headers()
    assert headers == ("filename", "category", "confidence")

def test_classification_result_row():
    result = ClassificationResult("test.pdf", "Invoice", 0.8)
    row = result.row()
    assert row == ("test.pdf", "Invoice", "0.80")

def test_write_results_csv(tmp_path):
    results = [
        ClassificationResult("doc1.pdf", "Invoice", 0.95),
        ClassificationResult("doc2.docx", "Contract", 1.0),
        ClassificationResult("doc3.pdf", "unknown", 0.4),
    ]
    
    output_path = tmp_path / "results.csv"
    write_results_csv(results, output_path)
    
    # Read back and verify
    with open(output_path) as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    
    assert len(rows) == 3
    assert rows[0] == {"filename": "doc1.pdf", "category": "Invoice", "confidence": "0.95"}
    assert rows[1]["confidence"] == "1.00"
    assert rows[2]["category"] == "unknown"

def test_write_creates_parent_directories(tmp_path):
    results = [ClassificationResult("test.pdf", "Invoice", 0.8)]
    
    output_path = tmp_path / "subdir" / "results.csv"
    write_results_csv(results, output_path)
    
    assert output_path.exists()

def test_write_permission_error(tmp_path):
    results = [ClassificationResult("test.pdf", "Invoice", 0.8)]
    
    # Create a directory instead of a file to cause permission error
    output_path = tmp_path / "is_a_directory"
    output_path.mkdir()
    
    with pytest.raises(OutputError):
        write_results_csv(results, output_path)
```

**Approach:** Use real temporary files (`tmp_path` fixture) and verify CSV format.

## Fixtures (conftest.py)

Shared fixtures in `tests/conftest.py`:

```python
import pytest
from categories import CategorySet, Category

@pytest.fixture
def sample_categories():
    """A sample category set for testing."""
    return CategorySet(categories=(
        Category("Invoice", "Invoice documents", ("Invoice example",)),
        Category("Contract", "Contracts", ("Contract example",)),
        Category("Report", "Reports", ("Report example",)),
    ))

@pytest.fixture
def sample_document_texts():
    """Sample documents for classification testing."""
    return {
        "invoice": "Invoice from vendor Inc. dated 2024-01-15. Amount: $1000.",
        "contract": "Service Agreement between Company A and Company B.",
        "report": "Quarterly Business Report Q1 2024.",
    }
```

## Pytest Markers

Tests are marked with `@pytest.mark.unit` (all tests are unit tests currently):

```python
@pytest.mark.unit
def test_something():
    ...
```

Run only unit tests:
```bash
uv run pytest -m unit
```

## CI/CD Integration

The `.github/workflows/unit-tests.yml` runs tests on every push and pull request.

See the workflow file for details on test execution in CI.

## Best Practices

1. **Mock external dependencies (API, filesystem)**
   ```python
   # GOOD: Mock the Anthropic client
   with patch("classifier.anthropic.Anthropic") as mock_client:
       ...
   
   # BAD: Real API call in tests
   client = anthropic.Anthropic(api_key="...")
   ```

2. **Use temporary directories for filesystem tests**
   ```python
   # GOOD
   def test_write_csv(tmp_path):
       output = tmp_path / "results.csv"
       ...
   
   # BAD
   def test_write_csv():
       with open("/tmp/test_results.csv") as f:
           ...  # Fragile, no cleanup
   ```

3. **Test both success and failure paths**
   ```python
   def test_classify_success():
       # Test the happy path
       ...
   
   def test_classify_error():
       # Test error handling
       with pytest.raises(ClassificationError):
           ...
   ```

4. **Use descriptive test names**
   ```python
   # GOOD
   def test_classify_with_unanimous_votes_returns_category_with_confidence_1_0():
       ...
   
   # OK (shorter)
   def test_unanimous_vote():
       ...
   
   # BAD
   def test_voting():
       ...  # Too vague
   ```

5. **Keep tests independent**
   ```python
   # GOOD: Each test stands alone
   def test_one(mock_classifier):
       mock_classifier.classify.side_effect = ["Invoice"]
       ...
   
   def test_two(mock_classifier):
       mock_classifier.classify.side_effect = ["Contract"]
       ...
   
   # BAD: Tests depend on execution order
   state = []
   def test_one():
       state.append("Invoice")
   def test_two():
       assert state == ["Invoice"]  # Fails if test_one doesn't run first
   ```

See `.claude/standards/testing.md` for the project's testing standards and [../architecture/overview.md](architecture/overview.md#testing-strategy) for the testing strategy overview.
