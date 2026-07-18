---
type: Workflow
title: Classification Pipeline
description: End-to-end flow of a document through the classifier system
resource: /src/main.py
tags: [workflow, classification, pipeline, orchestration]
---

# Classification Pipeline Workflow

This page traces the journey of a single document from enumeration through CSV output.

## High-Level Flow

```
1. User provides:
   - Source (local file/dir or SharePoint path)
   - Category definition (Markdown file)
   - Output CSV path

2. System:
   a. Parses category definitions → CategorySet (A1)
   b. Enumerates documents from source (A3)
   c. For each document:
      i.   Extract text (A2)
      ii.  Run SelfConsistencyClassifier N times (B1 + B2)
      iii. Collect (filename, category, confidence) result
   d. Write all results to CSV (A4)

3. User gets:
   - CSV with filename, category, confidence for each document
```

## Step-by-Step Details

### Step 1: Parse Categories (A1)

```python
categories = parse_category_file(Path("categories.md"))
```

**Input:** Markdown file with structure:
```markdown
## Invoice
Invoices, billing statements, receipts

- Account statement dated 2024
- Monthly billing document
- Receipt for services rendered

## Contract
Legal agreements, contracts, NDAs

- Service agreement
- Non-disclosure agreement
```

**Output:** `CategorySet` containing:
- Ordered tuple of `Category` objects (name, description, examples)
- Enum values: `('Invoice', 'Contract', 'unknown')`

**Failure mode:** Duplicate category names or invalid Markdown format raises `CategoryFileError`

See [../domain/category-parsing.md](../domain/category-parsing.md) for format details.

### Step 2: Build Classifier (B1)

```python
from classifier import Classifier
import anthropic

client = anthropic.Anthropic(api_key="...")
classifier = Classifier(categories, client, temperature=0.4)
```

**Initialization:**
- Renders the static **category block** (system prompt with definitions + few-shot examples)
- Builds the JSON schema enum with defined categories + `unknown`
- **Both are cached and reused across all subsequent calls in the run**

**Key constraint:** The category block is byte-identical on every call, so the Anthropic prompt-cache prefix stays valid.

### Step 3: Build Self-Consistency Classifier (B2)

```python
from self_consistency import SelfConsistencyClassifier

sc_classifier = SelfConsistencyClassifier(
    classifier,
    n=5,  # from CLASSIFIER_N
    confidence_threshold=0.6  # from CLASSIFIER_CONFIDENCE_THRESHOLD
)
```

### Step 4: Enumerate Documents (A3)

```python
from sources import LocalFileSystemSource

source = LocalFileSystemSource(Path("./documents/"))
documents = source.documents()  # Returns sorted list of Path objects
```

**Behavior:**
- Single file returns itself
- Directory is walked recursively (sorted for determinism)
- **Only** files with registered extractors are included
- Unsupported files (e.g., `.txt`, `.exe`) log a `WARNING` and are skipped
- Invalid/missing paths raise `SourceError`

### Step 5: Extract & Classify Each Document (A2 + B1 + B2)

For each document in the enumerated list:

```python
from extraction import extract_text
from writer import ClassificationResult

document_path = Path("./documents/invoice_2024.pdf")

# A2: Extract text
text = extract_text(document_path)  # Returns plain string

# B1 + B2: Classify with self-consistency
verdict = sc_classifier.classify(text)
# → Verdict(category='Invoice', confidence=0.8)

# Collect result
result = ClassificationResult(
    filename=str(document_path),
    category=verdict.category,
    confidence=verdict.confidence
)
```

**Extraction (A2):**
- `extract_text()` dispatches on file suffix (`.pdf` → `PdfTextExtractor`, `.docx` → `DocxTextExtractor`)
- Each extractor returns plain text
- Failures raise `ExtractionError` (chained from the underlying library error)

**Self-Consistency (B2):**
- Calls the inner `Classifier` **5 times** (configurable via `CLASSIFIER_N`)
- Collects the 5 labels: e.g., `['Invoice', 'Invoice', 'Contract', 'Invoice', 'Invoice']`
- Votes: modal label = 'Invoice' (count=4), confidence = 4/5 = 0.8
- Returns `Verdict(category='Invoice', confidence=0.8)`

**Tie-breaking:**
If the modal count ties with the runner-up (e.g., 2 'Invoice', 2 'Contract', 1 'Unknown'), the result is `Verdict(category='unknown', confidence=...)`

**Threshold:**
If confidence ≤ 0.6 (the default threshold), the result is `Verdict(category='unknown', confidence=...)`

### Step 6: Collect Results

After all documents are processed, you have a list of `ClassificationResult` objects:

```python
results = [
    ClassificationResult("invoice_2024.pdf", "Invoice", 0.8),
    ClassificationResult("contract_2024.docx", "Contract", 1.0),
    ClassificationResult("readme.txt", "unknown", 0.6),  # unsupported format → skipped
]
```

### Step 7: Write CSV (A4)

```python
from writer import write_results_csv

write_results_csv(results, Path("output/results.csv"))
```

**Output CSV:**
```
filename,category,confidence
invoice_2024.pdf,Invoice,0.80
contract_2024.docx,Contract,1.00
```

**Behavior:**
- Creates parent directories if needed
- Overwrites the target file
- Confidence is formatted to 2 decimal places
- Failures raise `OutputError`

## Error Recovery & Failure Modes

### Category File Errors
- **Missing file:** `CategoryFileError`
- **Duplicate category names:** `CategoryFileError`
- **No categories found:** `CategoryFileError`
- **Resolution:** Fail the run; do not proceed to document enumeration

### Source Errors
- **Invalid path (not a file or directory):** `SourceError`
- **Symlink loops:** Prevented by `rglob(recurse_symlinks=False)`
- **Resolution:** Fail the run; do not proceed to classification

### Extraction Errors
- **Unsupported format (e.g., `.txt`):** Skipped with `WARNING` during enumeration; **skipped with** `UnsupportedFormatError` if pointed at directly
- **Corrupt PDF:** `ExtractionError`
- **Missing file:** `ExtractionError`
- **Resolution:** Fail the current document; propagate the error to the CLI for handling (full run failure vs. partial output TBD)

### Classification Errors
- **API failure (timeout, quota):** `ClassificationError`
- **Invalid response format:** `ClassificationError`
- **Resolution:** Fail the current document; propagate the error to the CLI

### Output Errors
- **Permission denied on output path:** `OutputError`
- **Disk full:** `OutputError`
- **Resolution:** Fail the run; CSV not written

## Configuration Tuning Points

All tuning is in environment variables (or `.env` file):

| Setting | Env Var | Default | Range | Effect |
|---------|---------|---------|-------|--------|
| Self-consistency runs | `CLASSIFIER_N` | 5 | ≥ 1 | Number of classification passes per document |
| LLM temperature | `CLASSIFIER_TEMPERATURE` | 0.4 | [0.0, 1.0] | Higher = more variation across the N runs, lower = more deterministic |
| Confidence threshold | `CLASSIFIER_CONFIDENCE_THRESHOLD` | 0.6 | [0.0, 1.0] | If modal confidence ≤ this, resolve to `unknown` |

See [../operations/configuration.md](../operations/configuration.md) for details.

## Performance Considerations

- **Sequential processing:** v1 processes documents one at a time (no batching or concurrency)
- **Prompt caching:** The static category block is cached after the first API call, reducing latency and cost for subsequent calls
- **Target scale:** < 100 files per run
- **Future:** If volumes grow, revisit with an ADR (per spec/spec.md)

## Next Steps

- **[Category Parsing](../domain/category-parsing.md)** — Category Markdown format and parser
- **[Text Extraction](../domain/text-extraction.md)** — Extractor implementations and format support
- **[Self-Consistency](../domain/self-consistency.md)** — Voting algorithm and confidence scoring
- **[Configuration](../operations/configuration.md)** — Environment variables and settings management
