import json
from types import SimpleNamespace

import httpx
import pytest
from anthropic import APIError
from anthropic.types import TextBlock

from categories import parse_categories
from classifier import MODEL, Classifier, create_classifier
from config import Settings
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


# The ``_isolate_settings_env`` autouse fixture (conftest.py) clears every
# settings env var before each test, so these builders only set what they need.
def _settings(monkeypatch, env):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)


def _anthropic_settings(monkeypatch, *, api_key="sk-x", model="claude-haiku-4-5"):
    return _settings(
        monkeypatch,
        {"CLASSIFIER_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": api_key, "CLASSIFIER_ANTHROPIC_MODEL": model},
    )


def _foundry_settings(monkeypatch, *, resource="res", api_key="fk", model="claude-haiku-4-5", managed_identity=False):
    env = {"CLASSIFIER_PROVIDER": "foundry", "ANTHROPIC_FOUNDRY_RESOURCE": resource, "CLASSIFIER_FOUNDRY_MODEL": model}
    if managed_identity:
        env["CLASSIFIER_FOUNDRY_USE_MANAGED_IDENTITY"] = "true"
    if api_key is not None:
        env["ANTHROPIC_FOUNDRY_API_KEY"] = api_key
    return _settings(monkeypatch, env)


def test_create_classifier_anthropic_builds_first_party_client(mocker, monkeypatch):
    fake = mocker.patch("anthropic.Anthropic")
    classifier = create_classifier(_categories(), _anthropic_settings(monkeypatch, api_key="sk-live"))
    fake.assert_called_once_with(api_key="sk-live")
    assert isinstance(classifier, Classifier)


def test_create_classifier_foundry_uses_api_key_when_present(mocker, monkeypatch):
    fake = mocker.patch("anthropic.AnthropicFoundry")
    create_classifier(_categories(), _foundry_settings(monkeypatch, resource="my-res", api_key="fk-1"))
    fake.assert_called_once_with(resource="my-res", api_key="fk-1")


def test_create_classifier_foundry_uses_managed_identity_without_key(mocker, monkeypatch):
    fake_client = mocker.patch("anthropic.AnthropicFoundry")
    credential = mocker.patch("azure.identity.DefaultAzureCredential")
    token_provider = mocker.patch("azure.identity.get_bearer_token_provider", return_value="token-provider")

    settings = _foundry_settings(monkeypatch, resource="my-res", api_key=None, managed_identity=True)
    create_classifier(_categories(), settings)

    token_provider.assert_called_once_with(credential.return_value, "https://cognitiveservices.azure.com/.default")
    fake_client.assert_called_once_with(resource="my-res", azure_ad_token_provider="token-provider")


@pytest.mark.parametrize(
    ("settings_factory", "client_path", "expected_model"),
    [
        pytest.param(_anthropic_settings, "anthropic.Anthropic", "anth-model", id="anthropic"),
        pytest.param(_foundry_settings, "anthropic.AnthropicFoundry", "foundry-model", id="foundry"),
    ],
)
def test_create_classifier_forwards_provider_model(mocker, monkeypatch, settings_factory, client_path, expected_model):
    fake = mocker.patch(client_path)
    fake.return_value.messages.create.return_value = _response("Invoice")
    classifier = create_classifier(_categories(), settings_factory(monkeypatch, model=expected_model))

    classifier.classify("some text")

    assert fake.return_value.messages.create.call_args.kwargs["model"] == expected_model


def test_create_classifier_anthropic_without_key_raises(monkeypatch):
    # Provider credentials are enforced here, not at Settings load (all sections
    # are optional in config.py), so a provider with no credential fails loudly.
    settings = _settings(monkeypatch, {"CLASSIFIER_PROVIDER": "anthropic"})
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY is required"):
        create_classifier(_categories(), settings)


def test_create_classifier_foundry_without_config_raises(monkeypatch):
    settings = _settings(monkeypatch, {"CLASSIFIER_PROVIDER": "foundry"})
    with pytest.raises(ValueError, match="Foundry is not configured"):
        create_classifier(_categories(), settings)
