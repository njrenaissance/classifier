---
type: Domain Concept
title: Self-Consistency & Confidence
description: N-run voting algorithm, tie-breaking rules, and confidence scoring
resource: /src/self_consistency.py
tags: [self-consistency, voting, confidence, domain, classifaction]
---

# Self-Consistency & Confidence (B2)

The classifier core (B1) returns one label per call. This layer runs the classifier **N times** and votes to produce a confidence score.

## Why Self-Consistency?

The Anthropic Messages API (Claude Haiku 4.5) does **not** expose logprobs (log probabilities), which are the traditional way to measure model confidence. Instead, we measure agreement across multiple runs:

**If 5 runs return:** `['Invoice', 'Invoice', 'Contract', 'Invoice', 'Invoice']`
- **Modal label:** 'Invoice' (most frequent)
- **Modal count:** 4 out of 5
- **Confidence:** 4/5 = **0.8**

See [ADR-0005](../../spec/adr/0005-confidence-self-consistency.md) for the full decision.

## The Verdict Type

```python
@dataclass(frozen=True)
class Verdict:
    category: str      # A real category name or 'unknown'
    confidence: float  # Agreement rate in [0.0, 1.0]
```

A `Verdict` is the output of `SelfConsistencyClassifier.classify()`.

## Voting Algorithm

```python
class SelfConsistencyClassifier:
    def classify(self, document_text: str) -> Verdict:
        # 1. Call the inner classifier N times
        labels = [self._classifier.classify(document_text) 
                  for _ in range(self._n)]
        
        # 2. Vote on the labels
        return self._vote(labels)
    
    def _vote(self, labels: list[str]) -> Verdict:
        # Count frequency of each label
        counts = Counter(labels)
        
        # Get the most common and runner-up
        (modal_label, modal_count), *rest = counts.most_common()
        confidence = modal_count / self._n
        
        # Check for tie: if runner-up has same count as modal
        is_tie = bool(rest) and rest[0][1] == modal_count
        
        # Rule 1: Tie resolves to 'unknown'
        # Rule 2: Low confidence (≤ threshold) resolves to 'unknown'
        if is_tie or confidence <= self._confidence_threshold:
            return Verdict(category=UNKNOWN_CATEGORY, confidence=confidence)
        
        return Verdict(category=modal_label, confidence=confidence)
```

## Rules

### Rule 1: Tie-Breaking

If two or more labels have the **same highest count**, resolve to `unknown`:

**Example (N=5):**
```
Runs: ['Invoice', 'Invoice', 'Contract', 'Contract', 'Unknown']
Counts: Invoice=2, Contract=2, Unknown=1
Modal: Tie between 'Invoice' and 'Contract'
→ Verdict(category='unknown', confidence=0.4)
```

**Rationale:** A tie indicates genuine ambiguity. Rather than pick arbitrarily, report `unknown`.

### Rule 2: Confidence Threshold

If the modal confidence is **at or below** the configured threshold (default 0.6), resolve to `unknown`:

**Example (N=5, threshold=0.6):**
```
Runs: ['Invoice', 'Invoice', 'Invoice', 'Contract', 'Unknown']
Modal: 'Invoice', count=3, confidence=3/5=0.6
0.6 ≤ 0.6 (threshold) → Resolve to 'unknown'
→ Verdict(category='unknown', confidence=0.6)
```

**Rationale:** Low agreement indicates uncertainty. Flag for human review via `unknown`.

### Rule 2b: Combine Both

Both rules apply independently:

**Example (N=10, threshold=0.5):**
```
Runs: [Invoice, Invoice, Invoice, Invoice, Invoice,
       Invoice, Contract, Contract, Contract, Unknown]
Modal: 'Invoice', count=6, confidence=0.6
No tie (no other label has 6)
Confidence 0.6 > 0.5 (threshold) → Keep 'Invoice'
→ Verdict(category='Invoice', confidence=0.6)
```

## Configuration

```python
from self_consistency import SelfConsistencyClassifier
from config import get_settings

settings = get_settings()
# settings.self_consistency_n → from CLASSIFIER_N (default 5)
# settings.confidence_threshold → from CLASSIFIER_CONFIDENCE_THRESHOLD (default 0.6)

sc_classifier = SelfConsistencyClassifier(
    inner_classifier,
    n=settings.self_consistency_n,
    confidence_threshold=settings.confidence_threshold
)
```

### Tuning Parameters

| Setting | Env Var | Default | Effect |
|---------|---------|---------|--------|
| Self-consistency runs | `CLASSIFIER_N` | 5 | More runs = more accurate confidence but higher API cost & latency |
| Confidence threshold | `CLASSIFIER_CONFIDENCE_THRESHOLD` | 0.6 | Higher = more documents marked `unknown`; lower = more confident assertions |

See [../operations/configuration.md](../operations/configuration.md) for details.

## Temperature & Variation

The `SelfConsistencyClassifier` doesn't control temperature; it comes from the inner `Classifier`:

```python
classifier = Classifier(categories, client, temperature=0.4)
sc_classifier = SelfConsistencyClassifier(classifier, n=5, confidence_threshold=0.6)
```

**Temperature effect:**
- **Low (0.0):** Deterministic; all N runs return the same label (confidence = 1.0)
- **High (1.0):** High variation; labels differ across runs (lower confidence)
- **Default (0.4):** Moderate variation; some labels agree, some differ

The temperature is tunable via `CLASSIFIER_TEMPERATURE` (default 0.4). See [../operations/configuration.md](../operations/configuration.md).

## Confidence Semantics

Confidence is **NOT** the model's internal "certainty" — it is the **agreement rate** across runs:

| Confidence | Meaning | Example |
|-----------|---------|---------|
| 1.0 | Unanimous | All 5 runs: 'Invoice' |
| 0.8 | Strong agreement | 4/5 runs: 'Invoice' |
| 0.6 | Weak agreement | 3/5 runs: 'Invoice' (but ≤ threshold, may resolve to `unknown`) |
| < 0.6 | Weak or threshold violation | 2/5 runs: 'Invoice' → Resolves to `unknown` |
| `unknown` + any score | Ambiguity flagged | Either tie, low confidence, or no clear winner |

**For users:** Confidence is a signal for prioritizing human review. Higher confidence = more likely correct.

## Error Propagation

If any single run fails, the entire verdict fails:

```python
def classify(self, document_text: str) -> Verdict:
    labels = [self._classifier.classify(document_text) 
              for _ in range(self._n)]
    # If any run raises ClassificationError, it propagates here
    return self._vote(labels)
```

**Rationale:** A failed run invalidates the vote. Partial tallies are not allowed.

## Testing & Mocking

In tests, you can mock the inner classifier to return specific labels:

```python
from unittest.mock import Mock
from self_consistency import SelfConsistencyClassifier

mock_classifier = Mock()
mock_classifier.classify.side_effect = [
    'Invoice', 'Invoice', 'Invoice',  # 3/5 = 0.6
]

sc = SelfConsistencyClassifier(mock_classifier, n=5, confidence_threshold=0.6)
verdict = sc.classify("Some document text")
# → Verdict(category='unknown', confidence=0.6)  [tie or threshold rule]
```

## Integration Points

- **Pipeline:** `SelfConsistencyClassifier` is instantiated by the CLI and called for each document
- **Configuration:** N and threshold come from environment variables via `get_settings()`
- **Error handling:** If any run fails, propagate `ClassificationError` to the CLI
- **CSV output:** The `Verdict` is converted to a `ClassificationResult` row for CSV writing

See [../architecture/overview.md](../architecture/overview.md#b2-self-consistency--confidence) for the role of B2 in the system and [../workflows/classification-pipeline.md](../workflows/classification-pipeline.md#step-5-extract--classify-each-document-a2--b1--b2) for the pipeline context.
