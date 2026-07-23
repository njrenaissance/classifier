"""Microsoft Graph client — app-only auth, delta pagination, download (E3).

The Graph primitives shared by the two v2 jobs (ADR-0014 walker, ADR-0015
processor). Three responsibilities live here:

- **App-only auth** (ADR-0007): a client-credentials bearer token — managed
  identity in production, a client secret locally — attached to every request.
  The credential is *injected*, so unit tests fake it and no real tenant is
  needed.
- **Delta walk** (ADR-0014): :meth:`GraphClient.iter_delta` iterates
  ``/drives/{id}/root/delta``, yields every driveItem across pages by following
  ``@odata.nextLink``, and *returns* the terminal ``@odata.deltaLink`` (the
  generator's return value, read from ``StopIteration.value``).
- **Parse helpers + download** (ADR-0014/0015): :func:`matter_folder` and
  :func:`content_hash` are pure functions over a driveItem;
  :meth:`GraphClient.download` fetches a file's bytes into memory.

Auth, transport, and malformed-response failures are translated to
:class:`~errors.GraphError`, always chained, so a caller catches one domain type
instead of raw ``httpx``/Azure exceptions.

**Content hash (ADR-0017):** ``file.hashes`` exposes ``quickXorHash`` on
SharePoint / OneDrive-for-Business drives and ``sha256Hash`` mainly on personal
OneDrive, so :func:`content_hash` prefers ``quickXorHash`` and falls back to
``sha256Hash`` then ``crc32Hash`` — refining ADR-0014's SHA-256 assumption.
"""

from collections.abc import Generator, Mapping
from types import TracebackType
from typing import Any
from urllib.parse import unquote

import httpx
from azure.core.credentials import TokenCredential
from azure.core.exceptions import ClientAuthenticationError

from config import GraphSettings, get_graph_settings
from errors import GraphError

MATTER_ROOT_SENTINEL = "_root"  # driveItems outside a matter folder; walker treats as 'skipped'.
_MATTERS_SEGMENT = "Matters"
_ROOT_PATH_MARKER = "root:"
# quickXorHash first: it is what SharePoint / OneDrive-for-Business populate (ADR-0017).
_HASH_FIELDS = ("quickXorHash", "sha256Hash", "crc32Hash")
_REQUEST_TIMEOUT_SECONDS = 60.0  # generous read window for large document downloads.


def matter_folder(item: Mapping[str, Any]) -> str:
    """Return the matter folder a driveItem lives under, or the root sentinel.

    The matter is the first path segment beneath ``/Matters/`` in the item's
    ``parentReference.path`` (e.g. ``.../root:/Matters/Smith-2026-001/Discovery``
    → ``Smith-2026-001``). Items not under a matter folder — including the
    ``/Matters`` folder itself and the library root — return
    :data:`MATTER_ROOT_SENTINEL`.
    """
    path = item.get("parentReference", {}).get("path")
    if not isinstance(path, str):
        return MATTER_ROOT_SENTINEL
    beneath_root = path.split(_ROOT_PATH_MARKER, 1)[-1]
    # Graph percent-encodes parentReference.path; decode each segment so a folder
    # like "Smith & Jones" is not mistaken for the literal "Smith%20%26%20Jones".
    segments = [unquote(segment) for segment in beneath_root.split("/") if segment]
    if _MATTERS_SEGMENT in segments:
        matter_index = segments.index(_MATTERS_SEGMENT) + 1
        if matter_index < len(segments):
            return segments[matter_index]
    return MATTER_ROOT_SENTINEL


def content_hash(item: Mapping[str, Any]) -> str | None:
    """Return the driveItem's content hash, or ``None`` if it has none.

    Reads ``file.hashes`` preferring ``quickXorHash`` (ADR-0017), then
    ``sha256Hash``, then ``crc32Hash``. Folders and hashless items yield ``None``.
    """
    file_facet = item.get("file")
    if not isinstance(file_facet, Mapping):
        return None
    hashes = file_facet.get("hashes", {})
    if not isinstance(hashes, Mapping):
        return None
    for field in _HASH_FIELDS:
        value = hashes.get(field)
        if value:
            return str(value)
    return None


class GraphClient:
    """Authenticated Microsoft Graph access for delta walks and downloads.

    The Azure credential and the ``httpx`` client are injected so both the token
    boundary and the network boundary can be faked in unit tests. Every request
    carries a freshly acquired app-only bearer token (the credential caches it),
    and every failure is raised as a chained :class:`~errors.GraphError`.
    """

    def __init__(self, credential: TokenCredential, http: httpx.Client, *, scope: str, base_url: str) -> None:
        self._credential = credential
        self._http = http
        self._scope = scope
        self._base_url = base_url.rstrip("/")

    def close(self) -> None:
        """Close the underlying HTTP client, releasing its connection pool.

        Long-running or repeatedly-invoked jobs should call this (or use the
        client as a context manager) so sockets are not held until GC.
        """
        self._http.close()

    def __enter__(self) -> "GraphClient":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def iter_delta(self, drive_id: str, start_url: str | None = None) -> Generator[dict[str, Any], None, str]:
        """Yield every driveItem in the drive's delta, returning the deltaLink.

        Walks from ``start_url`` (a saved ``@odata.nextLink``/``deltaLink`` to
        resume from) or the initial ``/root/delta`` URL, following
        ``@odata.nextLink`` until the terminal page, then returns its
        ``@odata.deltaLink`` — accessible to the caller via ``StopIteration.value``.
        """
        url = start_url or f"{self._base_url}/drives/{drive_id}/root/delta"
        while True:
            payload = self._get_json(url)
            yield from payload.get("value") or []  # a page may carry an explicit "value": null
            next_link = payload.get("@odata.nextLink")
            if next_link is None:
                break
            url = next_link
        delta_link = payload.get("@odata.deltaLink")
        if delta_link is None:
            raise GraphError(f"Delta walk of drive {drive_id!r} ended without an @odata.deltaLink token.")
        return str(delta_link)

    def download(self, drive_id: str, drive_item_id: str) -> bytes:
        """Download a driveItem's bytes into memory (ADR-0015).

        ``GET /drives/{drive_id}/items/{drive_item_id}/content``; Graph redirects
        to a pre-authenticated URL, so redirects are followed (``httpx`` drops the
        bearer header across hosts). HTTP failures raise a chained
        :class:`~errors.GraphError`.
        """
        url = f"{self._base_url}/drives/{drive_id}/items/{drive_item_id}/content"
        try:
            response = self._http.get(url, headers=self._auth_header(), follow_redirects=True)
            response.raise_for_status()
        except httpx.HTTPError as err:
            raise GraphError(f"Graph download failed: GET {url}: {err}") from err
        return response.content

    def _auth_header(self) -> dict[str, str]:
        """Return the ``Authorization`` header, acquiring an app-only token."""
        try:
            token = self._credential.get_token(self._scope).token
        except ClientAuthenticationError as err:
            raise GraphError(f"Graph token acquisition failed: {err}") from err
        return {"Authorization": f"Bearer {token}"}

    def _get_json(self, url: str) -> dict[str, Any]:
        """GET ``url`` with auth and return the parsed JSON body."""
        try:
            response = self._http.get(url, headers=self._auth_header())
            response.raise_for_status()
        except httpx.HTTPError as err:
            raise GraphError(f"Graph request failed: GET {url}: {err}") from err
        try:
            body: dict[str, Any] = response.json()
        except ValueError as err:
            raise GraphError(f"Graph response was not valid JSON: GET {url}: {err}") from err
        return body


def _build_credential(settings: GraphSettings) -> TokenCredential:
    """Construct the app-only credential — managed identity, else client secret.

    ``azure.identity`` is imported lazily so a caller that injects its own
    credential into :class:`GraphClient` never pays for it. :class:`GraphSettings`
    validation guarantees the client-credentials trio in the non-managed path.
    """
    from azure.identity import ClientSecretCredential, DefaultAzureCredential

    if settings.use_managed_identity:
        return DefaultAzureCredential()
    if settings.tenant_id is None or settings.client_id is None or settings.client_secret is None:
        raise ValueError(  # pragma: no cover - GraphSettings validation already guarantees this
            "Graph app-only auth requires tenant_id, client_id and client_secret."
        )
    return ClientSecretCredential(
        tenant_id=settings.tenant_id,
        client_id=settings.client_id,
        client_secret=settings.client_secret.get_secret_value(),
    )


def create_graph_client(settings: GraphSettings | None = None) -> GraphClient:
    """Build a :class:`GraphClient` wired from :class:`GraphSettings`.

    The only place a real Azure credential and ``httpx`` client are constructed;
    everywhere else injects them so tests need no tenant or network.
    """
    settings = settings or get_graph_settings()
    credential = _build_credential(settings)
    http = httpx.Client(timeout=_REQUEST_TIMEOUT_SECONDS)
    return GraphClient(credential, http, scope=settings.token_scope, base_url=settings.base_url)
