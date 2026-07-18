import json
from types import SimpleNamespace

import httpx
import pytest
from anthropic import APIError
from anthropic.types import TextBlock

from categories import parse_categories
from classifier import MODEL, Classifier
from errors import ClassificationError

pytestmark = pytest.mark.unit

CATEGORY_MARKDOWN = """\
## Invoice
Billing documents requesting payment.

- Invoice #4521, total due $1,200
- Remittance advice for account 8891

## Contract
Legally binding agreements between parties.

- Master Services Agreement between Acme and Globex
"""


def _categories():
    return parse_categories(CATEGORY_MARKDOWN)


def _response(category: str, *, stop_reason: str = "end_turn"):
    """A minimal stand-in for an Anthropic ``Message`` structured-output reply."""
    block = TextBlock(type="text", text=json.dumps({"category": category}), citations=None)
    return SimpleNamespace(stop_reason=stop_reason, content=[block])


def _classifier(mocker, response=None, *, temperature: float = 0.4):
    client = mocker.Mock()
    if response is not None:
        client.messages.create.return_value = response
    return Classifier(_categories(), client, temperature=temperature), client


def test_returns_in_set_label(mocker):
    classifier, _ = _classifier(mocker, _response("Invoice"))
    assert classifier.classify("Please remit $1,200 by net 30.") == "Invoice"


def test_returns_unknown_when_model_picks_it(mocker):
    classifier, _ = _classifier(mocker, _response("unknown"))
    assert classifier.classify("A grocery list.") == "unknown"


def test_schema_constrains_output_to_the_enum(mocker):
    classifier, client = _classifier(mocker, _response("Invoice"))
    classifier.classify("some text")

    schema = client.messages.create.call_args.kwargs["output_config"]["format"]["schema"]
    assert schema["properties"]["category"]["enum"] == ["Invoice", "Contract", "unknown"]
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["category"]


def test_static_block_is_cached_and_document_text_is_the_suffix(mocker):
    classifier, client = _classifier(mocker, _response("Invoice"))
    classifier.classify("the document body")

    kwargs = client.messages.create.call_args.kwargs
    system_block = kwargs["system"][0]
    assert system_block["cache_control"] == {"type": "ephemeral"}
    assert "## Invoice" in system_block["text"]
    assert kwargs["messages"] == [{"role": "user", "content": "the document body"}]


def test_uses_pinned_model_and_forwards_temperature(mocker):
    classifier, client = _classifier(mocker, _response("Invoice"), temperature=0.7)
    classifier.classify("some text")

    kwargs = client.messages.create.call_args.kwargs
    assert kwargs["model"] == MODEL == "claude-haiku-4-5"
    assert kwargs["temperature"] == 0.7


def test_out_of_set_label_raises(mocker):
    classifier, _ = _classifier(mocker, _response("Receipt"))
    with pytest.raises(ClassificationError):
        classifier.classify("some text")


def test_refusal_stop_reason_raises(mocker):
    classifier, _ = _classifier(mocker, _response("Invoice", stop_reason="refusal"))
    with pytest.raises(ClassificationError):
        classifier.classify("some text")


def test_api_error_is_translated_and_chained(mocker):
    classifier, client = _classifier(mocker)
    api_error = APIError("boom", request=httpx.Request("POST", "https://api.anthropic.com"), body=None)
    client.messages.create.side_effect = api_error

    with pytest.raises(ClassificationError) as excinfo:
        classifier.classify("some text")
    assert excinfo.value.__cause__ is api_error
