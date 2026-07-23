"""Tests for the Microsoft Graph client (ADR-0007/0014/0015).

The network is an external boundary, so every case fakes the injected
``httpx.Client`` and the injected Azure credential — no real tenant or token is
ever needed (an issue-#40 acceptance criterion). The parse helpers are pure
functions exercised directly against driveItem fixtures.
"""

import httpx
import pytest
from azure.core.exceptions import ClientAuthenticationError

import graph_client
from config import GraphSettings, get_graph_settings
from errors import GraphError
from graph_client import GraphClient, content_hash, create_graph_client, matter_folder

pytestmark = pytest.mark.unit

_BASE_URL = "https://graph.microsoft.com/v1.0"
_SCOPE = "https://graph.microsoft.com/.default"
_DRIVE_ID = "drive-1"


def _response(mocker, *, json=None, content=b"", error=None):
    """A fake ``httpx.Response`` whose ``raise_for_status`` may raise ``error``."""
    response = mocker.Mock(spec=httpx.Response)
    response.json.return_value = json
    response.content = content
    response.raise_for_status.side_effect = error
    return response


def _client(mocker, http, *, token="access-token"):
    """A :class:`GraphClient` wired to a fake HTTP client and a fake credential."""
    credential = mocker.Mock()
    credential.get_token.return_value = mocker.Mock(token=token)
    return GraphClient(credential, http, scope=_SCOPE, base_url=_BASE_URL), credential


def _drain(generator):
    """Exhaust a delta generator, returning ``(items, delta_link)``."""
    items = []
    try:
        while True:
            items.append(next(generator))
    except StopIteration as stop:
        return items, stop.value


# --- delta pagination ------------------------------------------------------


@pytest.mark.parametrize(
    ("pages", "expected_ids"),
    [
        pytest.param(
            [{"value": [{"id": "a"}, {"id": "b"}], "@odata.deltaLink": "DELTA"}],
            ["a", "b"],
            id="single_page",
        ),
        pytest.param(
            [
                {"value": [{"id": "a"}], "@odata.nextLink": "PAGE2"},
                {"value": [{"id": "b"}], "@odata.nextLink": "PAGE3"},
                {"value": [{"id": "c"}], "@odata.deltaLink": "DELTA"},
            ],
            ["a", "b", "c"],
            id="three_pages",
        ),
    ],
)
def test_iter_delta_yields_every_item_and_returns_delta_link(mocker, pages, expected_ids):
    http = mocker.Mock()
    http.get.side_effect = [_response(mocker, json=page) for page in pages]
    client, _ = _client(mocker, http)

    items, delta_link = _drain(client.iter_delta(_DRIVE_ID))

    assert [item["id"] for item in items] == expected_ids
    assert delta_link == "DELTA"


def test_iter_delta_starts_from_the_default_url_then_follows_next_links(mocker):
    pages = [
        {"value": [{"id": "a"}], "@odata.nextLink": "https://graph/next"},
        {"value": [{"id": "b"}], "@odata.deltaLink": "DELTA"},
    ]
    http = mocker.Mock()
    http.get.side_effect = [_response(mocker, json=page) for page in pages]
    client, _ = _client(mocker, http)

    _drain(client.iter_delta(_DRIVE_ID))

    called_urls = [call.args[0] for call in http.get.call_args_list]
    assert called_urls == [f"{_BASE_URL}/drives/{_DRIVE_ID}/root/delta", "https://graph/next"]


def test_iter_delta_resumes_from_a_supplied_start_url(mocker):
    http = mocker.Mock()
    http.get.side_effect = [_response(mocker, json={"value": [], "@odata.deltaLink": "DELTA"})]
    client, _ = _client(mocker, http)

    _drain(client.iter_delta(_DRIVE_ID, start_url="https://graph/resume-here"))

    assert http.get.call_args_list[0].args[0] == "https://graph/resume-here"


def test_iter_delta_without_a_terminal_delta_link_raises_graph_error(mocker):
    http = mocker.Mock()
    http.get.side_effect = [_response(mocker, json={"value": [{"id": "a"}]})]
    client, _ = _client(mocker, http)

    with pytest.raises(GraphError):
        _drain(client.iter_delta(_DRIVE_ID))


def test_iter_delta_wraps_transport_errors_as_chained_graph_error(mocker):
    http = mocker.Mock()
    http.get.side_effect = httpx.ConnectError("connection refused")
    client, _ = _client(mocker, http)

    with pytest.raises(GraphError) as excinfo:
        _drain(client.iter_delta(_DRIVE_ID))
    assert isinstance(excinfo.value.__cause__, httpx.ConnectError)


def test_iter_delta_wraps_a_malformed_json_body_as_chained_graph_error(mocker):
    decode_error = ValueError("Expecting value: line 1 column 1 (char 0)")
    response = _response(mocker)
    response.json.side_effect = decode_error
    http = mocker.Mock()
    http.get.return_value = response
    client, _ = _client(mocker, http)

    with pytest.raises(GraphError) as excinfo:
        _drain(client.iter_delta(_DRIVE_ID))
    assert excinfo.value.__cause__ is decode_error


def test_iter_delta_tolerates_a_page_with_null_value(mocker):
    http = mocker.Mock()
    http.get.side_effect = [_response(mocker, json={"value": None, "@odata.deltaLink": "DELTA"})]
    client, _ = _client(mocker, http)

    items, delta_link = _drain(client.iter_delta(_DRIVE_ID))

    assert items == []
    assert delta_link == "DELTA"


# --- matter_folder ---------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        pytest.param("/drives/b!x/root:/Matters/Smith-2026-001/Discovery", "Smith-2026-001", id="nested_under_matter"),
        pytest.param("/drives/b!x/root:/Matters/Smith-2026-001", "Smith-2026-001", id="matter_root_folder"),
        pytest.param("/drives/b!x/root:/Matters", graph_client.MATTER_ROOT_SENTINEL, id="matters_root_itself"),
        pytest.param("/drives/b!x/root:/Admin/Templates", graph_client.MATTER_ROOT_SENTINEL, id="outside_matters"),
        pytest.param("/drives/b!x/root:/", graph_client.MATTER_ROOT_SENTINEL, id="library_root"),
        pytest.param("/drives/b!x/root:/Matters/Smith%20%26%20Jones/Discovery", "Smith & Jones", id="percent_encoded"),
    ],
)
def test_matter_folder_extracts_first_segment_under_matters(path, expected):
    item = {"parentReference": {"path": path}}
    assert matter_folder(item) == expected


@pytest.mark.parametrize(
    "item",
    [
        pytest.param({}, id="no_parent_reference"),
        pytest.param({"parentReference": {}}, id="parent_reference_without_path"),
    ],
)
def test_matter_folder_falls_back_to_sentinel_when_path_is_absent(item):
    assert matter_folder(item) == graph_client.MATTER_ROOT_SENTINEL


# --- content_hash ----------------------------------------------------------


@pytest.mark.parametrize(
    ("hashes", "expected"),
    [
        pytest.param({"quickXorHash": "QXH", "sha256Hash": "SHA"}, "QXH", id="prefers_quick_xor_hash"),
        pytest.param({"sha256Hash": "SHA"}, "SHA", id="falls_back_to_sha256"),
        pytest.param({"crc32Hash": "CRC"}, "CRC", id="falls_back_to_crc32"),
        pytest.param({}, None, id="empty_hashes"),
    ],
)
def test_content_hash_prefers_quick_xor_then_falls_back(hashes, expected):
    item = {"file": {"hashes": hashes}}
    assert content_hash(item) == expected


@pytest.mark.parametrize(
    "item",
    [
        pytest.param({}, id="no_file_facet"),
        pytest.param({"folder": {"childCount": 3}}, id="folder_item"),
        pytest.param({"file": {}}, id="file_without_hashes"),
        pytest.param({"file": {"hashes": "malformed"}}, id="hashes_not_a_mapping"),
    ],
)
def test_content_hash_is_none_without_a_hashed_file_facet(item):
    assert content_hash(item) is None


# --- download --------------------------------------------------------------


def test_download_returns_the_response_bytes(mocker):
    http = mocker.Mock()
    http.get.return_value = _response(mocker, content=b"PDF-BYTES")
    client, _ = _client(mocker, http)

    assert client.download(_DRIVE_ID, "item-42") == b"PDF-BYTES"
    assert http.get.call_args.args[0] == f"{_BASE_URL}/drives/{_DRIVE_ID}/items/item-42/content"


def test_download_surfaces_http_errors_as_chained_graph_error(mocker):
    request = httpx.Request("GET", "https://graph/content")
    status_error = httpx.HTTPStatusError("404", request=request, response=httpx.Response(404, request=request))
    http = mocker.Mock()
    http.get.return_value = _response(mocker, error=status_error)
    client, _ = _client(mocker, http)

    with pytest.raises(GraphError) as excinfo:
        client.download(_DRIVE_ID, "item-42")
    assert excinfo.value.__cause__ is status_error


# --- token acquisition (injectable, no real tenant) ------------------------


def test_requests_carry_the_injected_bearer_token(mocker):
    http = mocker.Mock()
    http.get.return_value = _response(mocker, content=b"")
    client, credential = _client(mocker, http, token="tenant-token")

    client.download(_DRIVE_ID, "item-42")

    credential.get_token.assert_called_with(_SCOPE)
    assert http.get.call_args.kwargs["headers"]["Authorization"] == "Bearer tenant-token"


def test_token_acquisition_failure_becomes_a_chained_graph_error(mocker):
    http = mocker.Mock()
    credential = mocker.Mock()
    auth_error = ClientAuthenticationError("no credential available")
    credential.get_token.side_effect = auth_error
    client = GraphClient(credential, http, scope=_SCOPE, base_url=_BASE_URL)

    with pytest.raises(GraphError) as excinfo:
        client.download(_DRIVE_ID, "item-42")
    assert excinfo.value.__cause__ is auth_error
    http.get.assert_not_called()


# --- lifecycle -------------------------------------------------------------


def test_close_closes_the_underlying_http_client(mocker):
    http = mocker.Mock()
    client, _ = _client(mocker, http)

    client.close()

    http.close.assert_called_once_with()


def test_context_manager_closes_on_exit(mocker):
    http = mocker.Mock()
    client, _ = _client(mocker, http)

    with client as entered:
        assert entered is client
    http.close.assert_called_once_with()


# --- create_graph_client factory -------------------------------------------


def test_factory_uses_client_secret_credential_for_app_only_auth(mocker):
    secret_credential = mocker.patch("azure.identity.ClientSecretCredential")
    managed_credential = mocker.patch("azure.identity.DefaultAzureCredential")
    settings = GraphSettings(tenant_id="tenant", client_id="client", client_secret="secret", use_managed_identity=False)

    client = create_graph_client(settings)

    assert isinstance(client, GraphClient)
    secret_credential.assert_called_once_with(tenant_id="tenant", client_id="client", client_secret="secret")
    managed_credential.assert_not_called()


def test_factory_uses_managed_identity_when_enabled(mocker):
    secret_credential = mocker.patch("azure.identity.ClientSecretCredential")
    managed_credential = mocker.patch("azure.identity.DefaultAzureCredential")
    settings = GraphSettings(use_managed_identity=True)

    client = create_graph_client(settings)

    assert isinstance(client, GraphClient)
    managed_credential.assert_called_once_with()
    secret_credential.assert_not_called()


def test_factory_defaults_to_the_cached_graph_settings(mocker, monkeypatch):
    monkeypatch.setenv("CLASSIFIER__GRAPH_USE_MANAGED_IDENTITY", "true")
    get_graph_settings.cache_clear()
    managed_credential = mocker.patch("azure.identity.DefaultAzureCredential")

    client = create_graph_client()

    assert isinstance(client, GraphClient)
    managed_credential.assert_called_once_with()
    get_graph_settings.cache_clear()
