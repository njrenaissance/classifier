import json
from types import SimpleNamespace

import pytest
from anthropic.types import TextBlock

from categories import parse_categories
from classifier import Classifier
from config import Settings
from self_consistency import (
    SelfConsistencyClassifier,
    Verdict,
    create_self_consistency_classifier,
)

pytestmark = pytest.mark.unit

CATEGORY_MARKDOWN = """\
## Invoice
Billing documents requesting payment.

- Invoice #4521, total due $1,200

## Contract
Legally binding agreements between parties.

- Master Services Agreement between Acme and Globex
"""


def _categories():
    return parse_categories(CATEGORY_MARKDOWN)


def _response(category: str, *, stop_reason: str = "end_turn"):
    """A minimal stand-in for an Anthropic ``Message`` structured-output reply.

    Mirrors the helper in ``test_classifier.py``: a real ``TextBlock`` (so the
    core's ``isinstance`` check passes) wrapped in a ``SimpleNamespace`` message.
    """
    block = TextBlock(type="text", text=json.dumps({"category": category}), citations=None)
    return SimpleNamespace(stop_reason=stop_reason, content=[block])


def _voter(mocker, labels, *, n, threshold):
    """Wrap a real ``Classifier`` (with a mocked client) in a voter.

    ``labels`` is scripted as the sequence of single-call replies, one consumed
    per ``Classifier.classify`` invocation via the client's ``side_effect``.
    """
    client = mocker.Mock()
    client.messages.create.side_effect = [_response(label) for label in labels]
    classifier = Classifier(_categories(), client, temperature=0.4)
    voter = SelfConsistencyClassifier(classifier, n=n, confidence_threshold=threshold)
    return voter, client


@pytest.mark.parametrize(
    ("labels", "n", "threshold", "expected"),
    [
        pytest.param(["Invoice"] * 5, 5, 0.6, Verdict("Invoice", 1.0), id="unanimous->real_label_1.0"),
        pytest.param(["Invoice"] * 4 + ["Contract"], 5, 0.6, Verdict("Invoice", 0.8), id="majority_above->real_0.8"),
        pytest.param(
            ["Invoice"] * 3 + ["Contract"] * 2, 5, 0.6, Verdict("unknown", 0.6), id="at_threshold->unknown_0.6"
        ),
        pytest.param(
            ["Invoice"] * 3 + ["Contract"] * 2, 5, 0.5, Verdict("Invoice", 0.6), id="above_lower_threshold->real_0.6"
        ),
        pytest.param(
            ["Invoice", "Invoice", "Contract", "Contract", "unknown"],
            5,
            0.6,
            Verdict("unknown", 0.4),
            id="tie->unknown_modal_rate_0.4",
        ),
        pytest.param(["unknown"] * 5, 5, 0.6, Verdict("unknown", 1.0), id="genuine_unknown_passthrough_1.0"),
    ],
)
def test_vote_produces_expected_verdict(mocker, labels, n, threshold, expected):
    voter, _ = _voter(mocker, labels, n=n, threshold=threshold)
    assert voter.classify("some document text") == expected


def test_runs_classifier_n_times(mocker):
    voter, client = _voter(mocker, ["Invoice"] * 3, n=3, threshold=0.6)
    voter.classify("some document text")
    assert client.messages.create.call_count == 3


def test_single_run_is_always_confident(mocker):
    voter, _ = _voter(mocker, ["Invoice"], n=1, threshold=0.6)
    assert voter.classify("some document text") == Verdict("Invoice", 1.0)


@pytest.mark.parametrize("bad_n", [0, -1])
def test_rejects_non_positive_n(mocker, bad_n):
    client = mocker.Mock()
    classifier = Classifier(_categories(), client, temperature=0.4)
    with pytest.raises(ValueError, match="must be >= 1"):
        SelfConsistencyClassifier(classifier, n=bad_n, confidence_threshold=0.6)


def test_factory_sources_n_and_threshold_from_settings(mocker, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("CLASSIFIER_N", "7")
    monkeypatch.setenv("CLASSIFIER_CONFIDENCE_THRESHOLD", "0.5")
    settings = Settings(_env_file=None)
    categories = _categories()
    inner = mocker.Mock(spec=Classifier)
    create_classifier = mocker.patch("self_consistency.create_classifier", return_value=inner)

    voter = create_self_consistency_classifier(categories, settings)

    create_classifier.assert_called_once_with(categories, settings)
    assert isinstance(voter, SelfConsistencyClassifier)
    assert voter._n == 7
    assert voter._confidence_threshold == 0.5
