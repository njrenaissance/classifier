import pytest

from categories import parse_categories
from errors import ClassificationError
from jev import MAX_INPUT_CHARS, JevClassifier, JevClientError

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


def _classifier(mocker, decision="Invoice", *, max_input_chars=MAX_INPUT_CHARS):
    client = mocker.Mock()
    client.decide.return_value = decision
    return JevClassifier(_categories(), client, max_input_chars=max_input_chars), client


def test_returns_in_set_label(mocker):
    classifier, _ = _classifier(mocker, "Invoice")
    assert classifier.classify("Please remit $1,200 by net 30.") == "Invoice"


def test_returns_unknown_when_client_picks_it(mocker):
    classifier, _ = _classifier(mocker, "unknown")
    assert classifier.classify("A grocery list.") == "unknown"


def test_answer_space_is_categories_plus_unknown(mocker):
    classifier, client = _classifier(mocker)
    classifier.classify("some text")

    options = client.decide.call_args.args[1]
    assert list(options) == ["Invoice", "Contract", "unknown"]


def test_document_text_is_sent_to_the_client(mocker):
    classifier, client = _classifier(mocker)
    classifier.classify("the document body")

    assert client.decide.call_args.args[0] == "the document body"


@pytest.mark.parametrize(
    "decision",
    [
        pytest.param("Receipt", id="not_in_categories"),
        pytest.param("invoice", id="wrong_case"),
        pytest.param("", id="empty"),
    ],
)
def test_out_of_set_answer_raises(mocker, decision):
    classifier, _ = _classifier(mocker, decision)
    with pytest.raises(ClassificationError):
        classifier.classify("some text")


def test_client_failure_is_translated_and_chained(mocker):
    classifier, client = _classifier(mocker)
    client_error = JevClientError("upstream unavailable")
    client.decide.side_effect = client_error

    with pytest.raises(ClassificationError) as excinfo:
        classifier.classify("some text")
    assert excinfo.value.__cause__ is client_error


def test_document_at_the_input_limit_is_sent(mocker):
    classifier, client = _classifier(mocker, max_input_chars=10)
    classifier.classify("0123456789")

    client.decide.assert_called_once()


def test_oversized_document_raises_without_calling_the_client(mocker):
    classifier, client = _classifier(mocker, max_input_chars=10)

    with pytest.raises(ClassificationError):
        classifier.classify("01234567890")
    client.decide.assert_not_called()
