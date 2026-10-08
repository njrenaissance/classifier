import pytest

from categories import parse_categories
from classifier import create_classifier
from config import Settings
from jev import JevClassifier

pytestmark = pytest.mark.unit

CATEGORY_MARKDOWN = """\
## Invoice
Billing documents requesting payment.

- Invoice #4521, total due $1,200
"""


def _categories():
    return parse_categories(CATEGORY_MARKDOWN)


def _settings(monkeypatch, env):
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return Settings()  # type: ignore[call-arg]  # values are resolved from env


def _jev_env():
    return {"CLASSIFIER__JEV_API_KEY": "jk-1", "CLASSIFIER__JEV_BASE_URL": "https://jev.example.com/v1"}


def test_create_classifier_builds_jev_classifier_with_jev_client(mocker, monkeypatch):
    http_client = mocker.patch("classifier.JevHttpClient")
    settings = _settings(monkeypatch, _jev_env())

    classifier = create_classifier(_categories(), settings)

    http_client.assert_called_once_with(api_key="jk-1", base_url="https://jev.example.com/v1")
    assert isinstance(classifier, JevClassifier)


def test_create_classifier_without_jev_config_raises(monkeypatch):
    settings = _settings(monkeypatch, {})

    with pytest.raises(ValueError, match="Jev is not configured"):
        create_classifier(_categories(), settings)
