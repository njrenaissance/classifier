import httpx2
import pytest
from typesafe_sdk import TypeSafeAPIError

from categories import parse_categories
from classification import Classification
from errors import ClassificationError
from jev import MAX_INPUT_CHARS, JevClassifier, JevClientError, JevHttpClient, JevResponse

pytestmark = pytest.mark.unit

CATEGORY_MARKDOWN = """\
## Invoice
Billing documents requesting payment.

- Invoice #4521, total due $1,200

## Contract
Legally binding agreements between parties.

- Master Services Agreement between Acme and Globex
"""

RAW_BODY = {"model": "jev-1", "answers": {"category": {"type": "choice", "choice": "Invoice"}}}


def _categories():
    return parse_categories(CATEGORY_MARKDOWN)


def _response(choice="Invoice", probabilities=None, raw=None):
    if probabilities is None:
        probabilities = {"Invoice": 0.82, "Contract": 0.11, "unknown": 0.07}
    return JevResponse(choice=choice, probabilities=probabilities, raw=RAW_BODY if raw is None else raw)


def _classifier(mocker, response=None, *, max_input_chars=MAX_INPUT_CHARS):
    client = mocker.Mock()
    client.decide.return_value = response or _response()
    return JevClassifier(_categories(), client, max_input_chars=max_input_chars), client


def test_returns_classification_with_probability_as_confidence(mocker):
    classifier, _ = _classifier(mocker)

    result = classifier.classify("Please remit $1,200 by net 30.")

    assert result == Classification(category="Invoice", confidence=0.82, raw=RAW_BODY)


def test_returns_unknown_with_its_probability(mocker):
    response = _response("unknown", {"Invoice": 0.1, "Contract": 0.2, "unknown": 0.7})
    classifier, _ = _classifier(mocker, response)

    assert classifier.classify("A grocery list.") == Classification(category="unknown", confidence=0.7, raw=RAW_BODY)


def test_raw_response_is_passed_through_unmodified(mocker):
    raw = {"id": "req-1", "answers": {"category": {"choice": "Invoice"}}, "extra": [1, 2]}
    classifier, _ = _classifier(mocker, _response(raw=raw))

    assert classifier.classify("some text").raw == raw


def test_criteria_are_categories_with_descriptions_plus_unknown(mocker):
    classifier, client = _classifier(mocker)
    classifier.classify("some text")

    criteria = client.decide.call_args.args[1]
    assert list(criteria) == ["Invoice", "Contract", "unknown"]
    assert criteria["Invoice"] == "Billing documents requesting payment."
    assert criteria["unknown"] == "None of the categories above fit the document."


def test_document_text_is_sent_to_the_client(mocker):
    classifier, client = _classifier(mocker)
    classifier.classify("the document body")

    assert client.decide.call_args.args[0] == "the document body"


@pytest.mark.parametrize(
    "response",
    [
        pytest.param(_response("Receipt", {"Receipt": 0.9}), id="not_in_categories"),
        pytest.param(_response("invoice", {"invoice": 0.9}), id="wrong_case"),
        pytest.param(_response("", {"": 0.9}), id="empty"),
    ],
)
def test_out_of_set_choice_raises(mocker, response):
    classifier, _ = _classifier(mocker, response)
    with pytest.raises(ClassificationError):
        classifier.classify("some text")


@pytest.mark.parametrize(
    "probabilities",
    [
        pytest.param({}, id="choice_missing_from_probabilities"),
        pytest.param({"Invoice": 1.5}, id="above_one"),
        pytest.param({"Invoice": -0.1}, id="below_zero"),
    ],
)
def test_invalid_probability_raises(mocker, probabilities):
    classifier, _ = _classifier(mocker, _response("Invoice", probabilities))
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


# --- JevHttpClient: the TypeSafe SDK adapter ---


def _sdk_response(mocker, *, choice="Invoice", probabilities=None, body=RAW_BODY):
    answer = mocker.Mock(choice=choice, probabilities=probabilities or {"Invoice": 0.82, "unknown": 0.18})
    response = mocker.Mock(choices={"category": answer})
    response.raw_http_response.json.return_value = body
    return response


def test_http_client_asks_one_choice_question_and_maps_the_answer(mocker):
    sdk = mocker.patch("jev.TypeSafeClient").return_value
    sdk.system_one.return_value = _sdk_response(mocker)
    client = JevHttpClient(api_key="jk-1", base_url="https://jev.example.com/v1")

    response = client.decide("the document", {"Invoice": "Bills.", "unknown": "None fit."})

    assert response == JevResponse(choice="Invoice", probabilities={"Invoice": 0.82, "unknown": 0.18}, raw=RAW_BODY)
    kwargs = sdk.system_one.call_args.kwargs
    assert kwargs["state"] == {"document": "the document"}
    question = kwargs["questions"]["category"]
    assert question.criteria == {"Invoice": "Bills.", "unknown": "None fit."}


def test_http_client_is_built_with_the_configured_key_and_endpoint(mocker):
    sdk_cls = mocker.patch("jev.TypeSafeClient")

    JevHttpClient(api_key="jk-1", base_url="https://jev.example.com/v1")

    sdk_cls.assert_called_once_with(api_key="jk-1", base_url="https://jev.example.com/v1")


def test_http_client_translates_sdk_errors_and_chains_them(mocker):
    sdk = mocker.patch("jev.TypeSafeClient").return_value
    sdk_error = TypeSafeAPIError(500, body=None, headers=httpx2.Headers(), message="upstream 500")
    sdk.system_one.side_effect = sdk_error
    client = JevHttpClient(api_key="jk-1", base_url="https://jev.example.com/v1")

    with pytest.raises(JevClientError) as excinfo:
        client.decide("the document", {"Invoice": None})
    assert excinfo.value.__cause__ is sdk_error


def test_http_client_rejects_a_response_without_the_category_answer(mocker):
    sdk = mocker.patch("jev.TypeSafeClient").return_value
    sdk.system_one.return_value = mocker.Mock(choices={})
    client = JevHttpClient(api_key="jk-1", base_url="https://jev.example.com/v1")

    with pytest.raises(JevClientError):
        client.decide("the document", {"Invoice": None})


def test_http_client_rejects_a_non_object_body(mocker):
    sdk = mocker.patch("jev.TypeSafeClient").return_value
    sdk.system_one.return_value = _sdk_response(mocker, body=["not", "an", "object"])
    client = JevHttpClient(api_key="jk-1", base_url="https://jev.example.com/v1")

    with pytest.raises(JevClientError):
        client.decide("the document", {"Invoice": None})
