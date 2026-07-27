"""Microsoft Graph client — app-only auth, delta pagination, download (E3).

The Graph primitives shared by the two v2 jobs (ADR-0014 walker, ADR-0015
processor). Three responsibilities live here:

- **App-only auth** (ADR-0007): a client-credentials bearer token — managed
  identity in production, a client secret locally — attached to every request.
  The credential is *injected*, so unit tests fake it and no real tenant is
  needed.
- **Delta walk** (ADR-0014): :meth:`GraphClient.iter_delta_pages` iterates
  ``/drives/{id}/root/delta`` one page at a time, surfacing each page's
  ``@odata.nextLink`` so a budgeted caller can persist a resume point at a page
  boundary; :meth:`GraphClient.iter_delta` is the flat item-level view over it.
  Both *return* the terminal ``@odata.deltaLink`` (the generator's return value,
  read from ``StopIteration.value``).
- **Parse helpers + download** (ADR-0014/0015): :func:`folder_path` and
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
from typing import Any, NamedTuple
from urllib.parse import quote

import httpx
from azure.core.credentials import TokenCredential
from azure.core.exceptions import ClientAuthenticationError

from config import GraphSettings, get_settings
from errors import GraphError

# quickXorHash first: it is what SharePoint / OneDrive-for-Business populate (ADR-0017).
_HASH_FIELDS = ("quickXorHash", "sha256Hash", "crc32Hash")
_REQUEST_TIMEOUT_SECONDS = 60.0  # generous read window for large document downloads.


class DeltaPage(NamedTuple):
    """One page of a delta walk: its driveItems and the link to the next page.

    ``next_link`` is the page's ``@odata.nextLink`` — the URL to fetch *after*
    this page — or ``None`` on the terminal page (whose ``@odata.deltaLink`` the
    walk returns instead). A budgeted walker persists ``next_link`` as its resume
    token when it stops between pages (ADR-0014).
    """

    items: list[dict[str, Any]]
    next_link: str | None


def folder_path(item: Mapping[str, Any]) -> str | None:
    """Return the driveItem's parent folder path, or ``None`` if absent.

    The raw Graph ``parentReference.path`` (e.g.
    ``/drives/{id}/root:/Matters/Smith-2026-001/Discovery``) is stored verbatim so
    a matter — or any other grouping — can be reconstructed from it later if
    needed. Classification itself is by document type and does not depend on the
    path, so no brittle per-segment parsing is done here (ADR-0018).
    """
    path = item.get("parentReference", {}).get("path")
    return path if isinstance(path, str) else None


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

    def iter_delta_pages(
        self, drive_id: str, start_url: str | None = None, *, root_path: str | None = None
    ) -> Generator[DeltaPage, None, str]:
        """Yield each delta page in turn, returning the terminal deltaLink.

        Walks from ``start_url`` (a saved ``@odata.nextLink``/``deltaLink`` to
        resume from) or the initial delta URL for ``root_path`` (ADR-0019): the
        whole drive when it is ``None``/empty, else the ``/root:/{path}:/delta``
        subtree. ``start_url`` takes precedence, so a scoped resume replays the
        exact page a saved token points at. Each yielded :class:`DeltaPage`
        carries that page's items and its ``@odata.nextLink`` (``None`` on the
        terminal page), so a caller can stop between pages and persist
        ``next_link`` as a resume token. The terminal page's ``@odata.deltaLink``
        is returned via ``StopIteration.value``.
        """
        url = start_url or self._initial_delta_url(drive_id, root_path)
        while True:
            payload = self._get_json(url)
            items = payload.get("value") or []  # a page may carry an explicit "value": null
            next_link = payload.get("@odata.nextLink")
            yield DeltaPage(items, next_link)
            if next_link is None:
                break
            url = next_link
        delta_link = payload.get("@odata.deltaLink")
        if delta_link is None:
            raise GraphError(f"Delta walk of drive {drive_id!r} ended without an @odata.deltaLink token.")
        return str(delta_link)

    def iter_delta(
        self, drive_id: str, start_url: str | None = None, *, root_path: str | None = None
    ) -> Generator[dict[str, Any], None, str]:
        """Yield every driveItem in the drive's delta, returning the deltaLink.

        The flat item-level view over :meth:`iter_delta_pages`: it flattens each
        page's items and forwards the terminal ``@odata.deltaLink`` — accessible
        to the caller via ``StopIteration.value``. ``root_path`` scopes the walk
        the same way (ADR-0019).
        """
        pages = self.iter_delta_pages(drive_id, start_url, root_path=root_path)
        while True:
            try:
                page = next(pages)
            except StopIteration as stop:
                return str(stop.value)
            yield from page.items

    def _initial_delta_url(self, drive_id: str, root_path: str | None) -> str:
        """Build the delta start URL for a fresh walk, scoped to ``root_path`` (ADR-0019).

        A ``None``/empty ``root_path`` (or a bare ``/``) walks the whole drive via
        ``/root/delta``; otherwise the walk is scoped to that subtree via Graph's
        path addressing, ``/root:/{path}:/delta``. Each path segment is
        percent-encoded while the segment separators are preserved, so a folder
        name with spaces addresses correctly.
        """
        base = f"{self._base_url}/drives/{drive_id}"
        relative = (root_path or "").strip("/")
        if not relative:
            return f"{base}/root/delta"
        return f"{base}/root:/{quote(relative, safe='/')}:/delta"

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

    def fetch_content_hash(self, drive_id: str, drive_item_id: str) -> str | None:
        """Return the driveItem's current content hash, or ``None`` if it has none.

        ``GET /drives/{drive_id}/items/{drive_item_id}?$select=file`` and reads the
        hash from the ``file`` facet via :func:`content_hash` (same preference
        order as the walker). The processor compares this against the work item's
        ``content_hash`` and skips a download when they diverge (ADR-0015), letting
        the walker re-enqueue. HTTP/JSON failures raise a chained
        :class:`~errors.GraphError`.
        """
        url = f"{self._base_url}/drives/{drive_id}/items/{drive_item_id}?$select=file"
        return content_hash(self._get_json(url))

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
    everywhere else injects them so tests need no tenant or network. Defaults to
    ``Settings.graph``, raising if Graph is unconfigured.
    """
    graph = settings or get_settings().graph
    if graph is None:
        raise ValueError("Graph is not configured; set the CLASSIFIER__GRAPH_* settings.")
    credential = _build_credential(graph)
    http = httpx.Client(timeout=_REQUEST_TIMEOUT_SECONDS)
    return GraphClient(credential, http, scope=graph.token_scope, base_url=graph.base_url)
