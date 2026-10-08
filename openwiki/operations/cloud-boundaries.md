---
type: Operations Guide
title: Cloud Service Boundaries and Integration Points
description: Integration points where the classifier connects to Microsoft Graph, Azure Queue Storage, PostgreSQL, and inference providers (Anthropic or Foundry), including seam abstractions, message contracts, and error handling.
tags: [cloud, integration, boundaries, graph, queue, database, inference, architecture]
verified:
  - by: openwiki/0.4.3
    at: 2026-08-28T19:49:26.700Z
sources:
  - id: openwiki-source-33e80d90e1243605a02c3c34
    resource: repo://src/classifier.py
  - id: openwiki-source-d502c275990c6476221bf080
    resource: repo://src/config.py
  - id: openwiki-source-8d1a30a0ada8a519a416e3a0
    resource: repo://src/content_source.py
  - id: openwiki-source-d49e5477b258f0c3fb829ea3
    resource: repo://src/db.py
  - id: openwiki-source-fbc7db9240740e6fed706532
    resource: repo://src/enqueuer.py
  - id: openwiki-source-3550f767b772eb8c14e5f44b
    resource: repo://src/errors.py
  - id: openwiki-source-a2690fbc7dfdd4c5929ecd6a
    resource: repo://src/filesystem_walker.py
  - id: openwiki-source-d80ccd5ced600aba9bc985a6
    resource: repo://src/graph_client.py
  - id: openwiki-source-d07d5f79924775126f96721c
    resource: repo://src/message_queue.py
  - id: openwiki-source-61a4c09bfce6828071a1f7dc
    resource: repo://src/models.py
  - id: openwiki-source-d6651d3bc51203d33893f15c
    resource: repo://src/processor.py
  - id: openwiki-source-0b08ad2ade4feed2ba3e8fa7
    resource: repo://src/walker.py
  - id: openwiki-source-c92b4a03744fca0d7a4dbedd
    resource: repo://src/writer.py
generated: { by: "openwiki/0.4.3", at: "2026-08-28T19:49:26.700Z" }
---

# Cloud Service Boundaries and Integration Points

The cloud pipeline (v2) integrates four external services through carefully abstracted boundaries: **Microsoft Graph** for SharePoint document enumeration and retrieval, **Azure Queue Storage** for walker→processor coupling, **PostgreSQL** for durable state, and **Anthropic or Azure Foundry** for inference. Each boundary defines a protocol, a concrete implementation, error translation, and authentication strategy.

## Overview: The Four Seams

The architecture decouples the two worker jobs through four boundaries:

1. **Graph Seam** (E3 / ADR-0015): Enumerate and download documents via Microsoft Graph delta queries. Used by the walker (producer) to discover changes and by the processor (consumer) to re-check hashes and download bytes.

2. **Queue Seam** (E5/E6 / ADR-0012): Azure Queue Storage decouples the walker from the processor. The walker enqueues a `Message` per changed file; the processor receives it, processes it, and deletes it on success.

3. **Database Seam** (E1 / ADR-0013): PostgreSQL stores the state machines (`SyncState` for walker progress, `Document` for per-file status, `ProcessingLog` for audit). The database is the system of record: source of truth on identity, content hash, and classification results.

4. **Inference Seam** (B1): Pluggable choice of Anthropic (direct API) or Azure Foundry (managed or API-key auth), selected at runtime. Enforced when building a client, not at settings load time, so jobs that don't classify (the walker) don't need inference credentials.

## Architecture Diagram

<!-- openwiki: mermaid parse failed and this diagram was converted to a text fence so it does not break rendering. Fix the diagram source and restore the mermaid fence. Parser error: Heuristic: an unescaped angle bracket inside a label breaks rendering; rephrase the label. -->
```text
graph TB
    subgraph Walker["Walker (Producer, E5)"]
        WGD["Graph Delta<br/>pages + items"]
        WEQ["Enqueuer:<br/>new/changed<br/>decisions"]
        WSQ["Message Queue<br/>(send)"]
    end
    
    subgraph Processor["Processor (Consumer, E6)"]
        PRQ["Message Queue<br/>(receive)"]
        PCS["ContentSource:<br/>hash re-check<br/>+ download"]
        PCLASS["Classifier:<br/>Inference"]
        PDB["DatabaseWriter:<br/>UPSERT"]
    end
    
    subgraph CloudServices["External Services"]
        Graph["Microsoft Graph<br/>SharePoint/OneDrive"]
        Queue["Azure Queue<br/>Storage"]
        DB["PostgreSQL<br/>State Store"]
        Infer["Anthropic API<br/>or<br/>Azure Foundry"]
    end
    
    WGD -->|iter_delta_pages| Graph
    WEQ -->|enqueue| WSQ
    WSQ -->|Message<br/>Pydantic JSON| Queue
    PRQ -->|receive| Queue
    PCS -->|fetch_content_hash<br/>download| Graph
    PCLASS -->|classify| Infer
    PDB -->|UPSERT| DB
    WGD -->|SyncState<br/>delta/resume| DB
    WEQ -->|Document<br/>queued| DB
```

---

## Microsoft Graph Seam (E3 / ADR-0007, ADR-0014, ADR-0015, ADR-0017)

The `GraphClient` abstraction unifies all Graph communication and error translation. It owns three responsibilities:

### 1. App-Only Authentication (ADR-0007)

Every Graph request carries a fresh app-only bearer token. The credential is injected at construction so tests fake it; at runtime, it is built by `_build_credential` from `GraphSettings`:

- **Managed Identity** (production): `DefaultAzureCredential()` acquires a token for Entra ID
- **Client Secret** (local dev): `ClientSecretCredential(tenant_id, client_id, client_secret)` for explicit credentials

Configuration section:
```
CLASSIFIER__GRAPH_USE_MANAGED_IDENTITY=true|false
CLASSIFIER__GRAPH_TENANT_ID=...        (client-secret mode)
CLASSIFIER__GRAPH_CLIENT_ID=...        (client-secret mode)
CLASSIFIER__GRAPH_CLIENT_SECRET=...    (client-secret mode)
CLASSIFIER__GRAPH_TOKEN_SCOPE=https://graph.microsoft.com/.default
CLASSIFIER__GRAPH_BASE_URL=https://graph.microsoft.com/v1.0
```

A partially configured section (e.g., `TENANT_ID` without `CLIENT_SECRET`) fails loudly at load time (raised by `GraphSettings._reject_partial`). A wholly absent section resolves to `Settings.graph = None`, so SharePoint pipelines can be disabled by omitting all Graph env vars.

### 2. Delta Pagination & Resume/Delta Tokens (ADR-0014)

`iter_delta_pages()` walks a document library's change feed one page at a time:

```python
generator = graph.iter_delta_pages(drive_id, start_url=None, root_path="/Matters")
for page in generator:
    # page.items: list of driveItems on this page
    # page.next_link: @odata.nextLink (None on terminal page)
    ...
delta_link = generator.send(None)  # or: StopIteration.value
```

**Resume/Delta Tokens** (from `SyncState` ORM model):
- `delta_token` (`@odata.deltaLink`): Stored only when a walk **completes**. The next full walk resumes from this token, so it is the watermark for "everything before this point is synced."
- `resume_token` (`@odata.nextLink`): Stored when a walk is **interrupted** mid-pagination due to time budget exhaustion. Points to the next page to fetch.

**Start priority**: `resume_token` > `delta_token` > full enumeration (query with no token). This ensures:
- A large first enumeration spreads across multiple time-budgeted jobs
- Interruptions resume at a page boundary (never mid-page)
- A completed delta can be resumed later without re-enumerating everything

**Root-path scoping** (ADR-0019): The walk is scoped at the Graph level via `root_path`:
- `None` or `"/"` → whole drive: `GET /drives/{id}/root/delta`
- `"/Matters"` → subtree: `GET /drives/{id}/root:/{percent-encoded path}:/delta`

Each path segment is percent-encoded while `/` separators are preserved, so folder names with spaces address correctly.

### 3. Content Hash Retrieval (ADR-0017)

The walker captures the content hash from each driveItem on the delta walk. On re-classification (processor re-check), `fetch_content_hash()` re-reads the current hash from Graph to detect stale messages:

```python
hash_now = graph.fetch_content_hash(drive_id, drive_item_id)
if hash_now != message.content_hash:
    # File changed since enqueue; processor skips, walker re-enqueues
```

**Hash preference order** (ADR-0017):
1. `quickXorHash` — populated on SharePoint and OneDrive-for-Business
2. `sha256Hash` — populated on personal OneDrive
3. `crc32Hash` — fallback
4. `None` — item is a folder or has no hash

Folders and hashless items are never enqueued.

### 4. Download (ADR-0015)

```python
bytes = graph.download(drive_id, drive_item_id)
```

Issues `GET /drives/{id}/items/{id}/content`; Graph redirects to a pre-signed URL. Redirects are followed (httpx drops auth headers across hosts by default, but Graph's pre-auth URL needs no header). HTTP failures raise chained `GraphError`.

### Error Translation

Every Graph failure (auth, HTTP, JSON parsing) is raised as a chained `GraphError` at the boundary:

```python
try:
    response = self._http.get(url, headers=self._auth_header())
    response.raise_for_status()
except httpx.HTTPError as err:
    raise GraphError(f"Graph request failed: GET {url}: {err}") from err
```

This means the walker, processor, and other callers catch one domain type (`GraphError`) instead of raw `httpx` or `azure.core.exceptions` types.

---

## Azure Queue Seam (E5/E6 / ADR-0012)

The `MessageQueue` abstraction decouples the walker (producer) from the processor (consumer). At-least-once delivery becomes exactly-once when combined with the database UPSERT.

### Message Wire Format

A `Message` (Pydantic model) is the only thing walker and processor share. It carries the file's identity and metadata, **never the bytes** (which exceed the queue's 64 KB limit per ADR-0015). Wire format is Pydantic JSON:

```python
# Walker: enqueue
message = Message(
    source=MessageSource.sharepoint,  # or MessageSource.filesystem
    document_id=123,
    sync_state_id=45,
    drive_id="b!...",                 # Graph drive id (SharePoint path)
                                       # or "filesystem:/path/to/root" (filesystem path)
    drive_item_id="01ABCD...",        # Graph item id (SharePoint path)
                                       # or "path/to/file.pdf" (filesystem path, relative to root)
    file_name="invoice.pdf",
    mime_type="application/pdf",
    content_hash="abc123def456...",    # quickXorHash / sha256 / crc32, depending on source
    enqueued_at=datetime(2026, 8, 28, 12, 0, 0, tzinfo=UTC),  # required to be timezone-aware
)
backend.send_message(message.model_dump_json())

# Processor: receive
raw_message = backend.receive_message()
message = Message.model_validate_json(raw_message.content)
```

**Message source** (ADR-0020): The `source` field (SharePoint or filesystem) is self-describing so:
- The processor can fail fast if a queue's messages don't match the configured source
- The same queue infrastructure supports both walker variants

### Send, Receive, Delete

#### `MessageQueue.enqueue(message)`

Serializes to JSON and sends to the queue. Idempotency is the **walker's** concern (via the enqueuer's "already queued" check); the queue just performs the send and translates any `AzureError` to chained `QueueError`.

#### `MessageQueue.receive(visibility_timeout=None)`

Pulls one message or returns `None` when the queue is empty. Parses the body back to `Message` via Pydantic; a malformed item is a queue boundary failure (raises chained `QueueError`) not a silent skip. Returns a `ReceivedMessage` that wraps the `Message` plus the delete handle and Azure's `dequeue_count` (the retry counter):

```python
@dataclass(frozen=True)
class ReceivedMessage:
    message: Message
    message_id: str          # needed for delete
    pop_receipt: str         # needed for delete
    dequeue_count: int       # poison-message counter (ADR-0014)
```

**Visibility timeout**: When a message is received, Azure hides it from other receivers for the specified duration (seconds). If the processor crashes, the message reappears after the timeout and is redelivered. The processor's `visibility_timeout` is configured at the boundary.

#### `MessageQueue.delete(received)`

Removes the message once the processor commits its result. Uses the `message_id` and `pop_receipt` from the received wrapper.

### Error Translation

Transport, auth, and malformed-message failures are all raised as chained `QueueError`:

```python
try:
    self._backend.send_message(...)
except AzureError as err:
    raise QueueError(...) from err

try:
    message = Message.model_validate_json(raw.content)
except ValidationError as err:
    raise QueueError(f"Queue message {raw.id} is not a valid work item") from err
```

### Queue Configuration

Configuration section:
```
CLASSIFIER__QUEUE_NAME=classifier-queue
# Auth mode 1: Connection string (local dev / Azurite)
CLASSIFIER__QUEUE_CONNECTION_STRING=DefaultEndpointsProtocol=...
# Auth mode 2: Managed identity (production)
CLASSIFIER__QUEUE_ACCOUNT_URL=https://myaccount.queue.core.windows.net
CLASSIFIER__QUEUE_USE_MANAGED_IDENTITY=true
```

Either a connection string *or* (account URL + managed identity) is required. A partially configured section fails loudly.

---

## PostgreSQL Database Seam (E1 / ADR-0013, ADR-0014)

The database is the system of record for state and results. SQLAlchemy ORM models live in `db.py`; Pydantic transfer objects (`DocumentClassification`, `Message`) live in `models.py`.

### State Models

#### `SyncState` — Walker Position Per Document Library

```python
class SyncState(Base):
    id: Mapped[int]                              # PK
    drive_id: Mapped[str]                        # unique; "b!..." or "filesystem:/..."
    delta_token: Mapped[str | None]              # @odata.deltaLink (stored on completion)
    resume_token: Mapped[str | None]             # @odata.nextLink (stored on interruption)
    walk_status: Mapped[WalkStatus]              # idle | walking | interrupted | completed
    last_synced_at: Mapped[datetime | None]      # when walk completed (distinct from updated_at)
    created_at, updated_at: Mapped[datetime]
```

**Lifecycle**:
1. Walker marks `walk_status = walking`, starts delta walk
2. On interruption: stores `resume_token`, marks `interrupted`
3. On completion: stores `delta_token`, clears `resume_token`, marks `completed`, stamps `last_synced_at`

The `delta_token` watermark ensures "everything up to this point is synced." A subsequent walk resumes from `resume_token` if present, else `delta_token`, else starts fresh.

#### `Document` — Per-File Status and Classification Result

```python
class Document(Base):
    id: Mapped[int]                                  # PK
    sync_state_id, drive_item_id: Mapped[str]      # unique pair (UPSERT conflict key)
    
    # Identity & metadata
    file_name: Mapped[str | None]
    mime_type: Mapped[str | None]
    folder_path: Mapped[str | None]                # parent path from Graph or relative path
    
    # Content change detection
    content_hash: Mapped[str | None]               # current hash
    previous_hash: Mapped[str | None]              # rotated on hash change
    
    # Lifecycle
    status: Mapped[DocumentStatus]                 # queued | processing | completed | skipped | pending | failed
    
    # Classification result (never overwritten if override is set)
    category: Mapped[str | None]
    confidence: Mapped[float | None]
    classification_override: Mapped[str | None]    # manual override label (ADR-0014)
    classified_by: Mapped[str | None]              # classifier.MODEL
    
    # Failure tracking
    error_message: Mapped[str | None]
    retry_count: Mapped[int]                       # observed counter (queue's dequeue_count is canonical)
    
    graph_modified_at, classified_at, processed_at: Mapped[datetime | None]
    created_at, updated_at: Mapped[datetime]
```

**Unique constraint**: `(sync_state_id, drive_item_id)` — the UPSERT conflict key. A file is uniquely identified by its library and item id (or relative path for filesystem).

**Status lifecycle**:
- `queued`: Enqueued by walker; waiting for processor
- `processing`: Processor marked it before processing; used to detect concurrent access
- `completed`: Classification succeeded; result in `category` and `confidence`
- `skipped`: Unsupported format (no extractor), or hash mismatch (stale message), or override present
- `pending`: Manual re-classification requested (e.g., a taxonomy change); walker re-enqueues on next run
- `failed`: Classification attempt failed; error in `error_message`; queue's `dequeue_count` governs retry/poison shedding

#### `ProcessingLog` — Per-Attempt Audit Trail

```python
class ProcessingLog(Base):
    id: Mapped[int]
    document_id: Mapped[int]                       # FK to Document
    attempt: Mapped[int]                           # 1-indexed
    status: Mapped[str]                            # "completed" | "skipped" | "failed"
    category, confidence: Mapped[str | None]
    error: Mapped[str | None]
    input_tokens, output_tokens: Mapped[int | None]
    cost_usd: Mapped[Decimal | None]
    created_at: Mapped[datetime]
```

One row per classification attempt (retries append, don't mutate `documents`). Captures outcome, label/confidence, error message, and tokens/cost for billing and observability.

### UPSERT Logic (ADR-0013)

The processor's `DatabaseWriter` performs an atomic UPSERT on conflict of `(sync_state_id, drive_item_id)`:

```python
INSERT INTO documents (sync_state_id, drive_item_id, category, confidence, ...)
VALUES (...)
ON CONFLICT (sync_state_id, drive_item_id) DO UPDATE SET
    category = EXCLUDED.category,
    confidence = EXCLUDED.confidence,
    ...
    classified_by = 'classifier.MODEL',
    classified_at = now(),
    processed_at = now(),
    updated_at = now()
WHERE classification_override IS NULL
```

**Key invariant**: The `WHERE classification_override IS NULL` guard makes the update a no-op when a manual override exists. The classifier **never** overwrites a human decision (ADR-0014).

**Conflict resolution**:
- New file → INSERT a new `Document` row
- Hash changed → UPDATE the existing row, rotating old hash to `previous_hash`
- Override present → INSERT but DO NOTHING on UPDATE (the guard makes it a no-op)

### Database Configuration

Configuration section:
```
CLASSIFIER__DATABASE_URL=postgresql://user:pass@host/dbname
```

The URL is a `SecretStr` (never logged). The engine is built lazily on first use via `get_engine()`, applying `pool_pre_ping=True` to detect stale pooled connections (important for Azure's burstable Postgres instances).

---

## Content Retrieval Seam (ADR-0020)

The processor reads content through the `ContentSource` protocol, which is implemented per source:

```python
class ContentSource(Protocol):
    def fetch_content_hash(self, message: Message) -> str | None: ...
    def download(self, message: Message) -> bytes: ...
```

Both methods take the whole `Message`, so each implementation reads the locator fields it needs.

### `GraphContentSource` (SharePoint Path)

Forwards to the `GraphClient`:

```python
class GraphContentSource:
    def fetch_content_hash(self, message: Message) -> str | None:
        return self._graph.fetch_content_hash(message.drive_id, message.drive_item_id)
    
    def download(self, message: Message) -> bytes:
        return self._graph.download(message.drive_id, message.drive_item_id)
```

Reads the Graph-provided hash (quickXorHash, sha256, or crc32) and downloads via Graph.

### `FilesystemContentSource` (Filesystem Path)

Resolves the message's `drive_item_id` (a root-relative POSIX path) against the mounted root and reads from disk:

```python
class FilesystemContentSource:
    def fetch_content_hash(self, message: Message) -> str | None:
        return hash_bytes(self.download(message))  # hash the bytes
    
    def download(self, message: Message) -> bytes:
        path = self._resolve(message.drive_item_id)
        return path.read_bytes()
    
    def _resolve(self, locator: str) -> Path:
        # locator must be relative; reject absolute or escaping paths
        candidate = Path(locator)
        if candidate.is_absolute():
            raise SourceError(...)
        resolved = (self._root / candidate).resolve()
        # check resolved is still under root (no ".." escape)
```

**Hash function**: Both `FilesystemWalker` (enqueue-time) and `FilesystemContentSource` (processor re-check) use `hash_bytes(data)` from `content_source.py`, which computes `sha256` of the bytes. This shared function is the invariant that allows mismatch detection: if a file's bytes changed since enqueue, the hash changes.

### Source Selection (Configuration-Driven)

```python
CLASSIFIER_SOURCE=sharepoint|filesystem
```

The processor's `run()` function branches:

```python
if settings.source == "filesystem":
    source = FilesystemContentSource(settings.filesystem.root)
else:
    source = GraphContentSource(graph_client)
processor = Processor(session, source, queue, voter, writer)
```

This keeps the core processor code source-agnostic. The boundary is injected, not compiled in.

---

## Inference Seam (B1 / ADR-0002, ADR-0008)

The `Classifier` performs one classification API call and returns one in-set label. Provider selection is configuration-driven; the client is built from credentials only when needed (so walker jobs don't require an API key).

### Provider Selection

```python
CLASSIFIER_PROVIDER=anthropic|foundry  # default: anthropic
```

`Settings.provider` is a `Literal["anthropic", "foundry"]`. The actual client (`anthropic.Anthropic` or `anthropic.AnthropicFoundry`) is built by `_build_client()` only when a classifier is needed (in the processor, not in the walker).

### Anthropic (Direct API)

Configuration:
```
CLASSIFIER_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...                    # required
CLASSIFIER_ANTHROPIC_MODEL=claude-haiku-4-5    # optional; default from DEFAULTS
```

The `AnthropicSettings` section is configured iff `ANTHROPIC_API_KEY` is present. A client is built directly:

```python
client = anthropic.Anthropic(api_key=settings.anthropic.api_key.get_secret_value())
```

### Foundry (Azure AI Foundry)

Configuration:
```
CLASSIFIER_PROVIDER=foundry
ANTHROPIC_FOUNDRY_RESOURCE=my-resource         # required

# Auth mode 1: Managed Identity (production)
CLASSIFIER_FOUNDRY_USE_MANAGED_IDENTITY=true

# Auth mode 2: API Key (local dev)
CLASSIFIER_FOUNDRY_USE_MANAGED_IDENTITY=false
ANTHROPIC_FOUNDRY_API_KEY=...                  # required if not using managed identity

CLASSIFIER_FOUNDRY_MODEL=claude-haiku-4-5      # optional; default from DEFAULTS
CLASSIFIER_FOUNDRY_TOKEN_SCOPE=https://cognitiveservices.azure.com/.default  # optional
```

The `FoundrySettings` section is configured iff a `resource` and a **usable credential** (API key *or* `use_managed_identity=true`) are present. A partially configured section (e.g., resource with no credential) fails loudly.

**Managed identity mode**:
```python
from azure.identity import DefaultAzureCredential, get_bearer_token_provider

token_provider = get_bearer_token_provider(
    DefaultAzureCredential(),
    foundry.token_scope
)
client = anthropic.AnthropicFoundry(
    resource=foundry.resource,
    azure_ad_token_provider=token_provider
)
```

**API key mode**:
```python
client = anthropic.AnthropicFoundry(
    resource=foundry.resource,
    api_key=foundry.api_key.get_secret_value()
)
```

### The Classifier API

```python
class Classifier:
    def __init__(self, categories: CategorySet, client: anthropic.Anthropic, *, model: str, temperature: float):
        self._client = client
        self._model = model
        self._temperature = temperature
        # Static category block (system prompt) is cached across calls (ADR-0008)
        self._system_block = _render_category_block(categories)
        self._schema = {...}  # JSON schema for structured output
    
    def classify(self, document_text: str) -> str:
        """Return one in-set category label."""
        response = self._client.messages.create(
            model=self._model,
            max_tokens=64,
            temperature=self._temperature,
            system=[{"type": "text", "text": self._system_block, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": document_text}],
            output_config={"format": {"type": "json_schema", "schema": self._schema}},
        )
        return self._extract_label(response)
```

**Prompt caching** (ADR-0008): The static category block (definitions + examples) is set as a cache prefix with `"cache_control": {"type": "ephemeral"}`, so the prefix (and its 90% token discount) stays valid across all calls in a run as long as the category set is byte-identical.

**Structured output**: The schema constrains the model to one of the allowed category labels; the model can never invent a label.

**One call, one label**: This module owns only the single API call. Self-consistency (N-run voting) lives in the `SelfConsistencyClassifier` caller.

### Error Translation

API failures raise chained `ClassificationError`:

```python
try:
    response = self._client.messages.create(...)
except anthropic.APIError as err:
    raise ClassificationError(f"Classification API call failed: {err}") from err
```

Malformed responses (missing text block, invalid JSON, out-of-set label) also raise `ClassificationError`.

---

## Integrated Error Handling

All external-service failures are translated to domain error types at their boundary:

| Boundary | SDK Exception | Domain Type | Examples |
|----------|---------------|-------------|----------|
| Graph | `httpx.HTTPError`, `ClientAuthenticationError` | `GraphError` | Token failure, HTTP error, JSON parse failure |
| Queue | `AzureError`, `ValidationError` | `QueueError` | Transport failure, malformed message |
| Database | `SQLAlchemyError` | `PersistenceError` | Connection failure, constraint violation |
| Inference | `anthropic.APIError` | `ClassificationError` | API call failure, malformed response |
| Filesystem (processor) | `OSError` | `SourceError` | File not found, permission denied |

Each domain error type is always **chained** (raised `from` the original), so the root cause survives in the traceback and logs. Callers catch one domain type instead of raw SDK exceptions.

---

## Entrypoint Wiring: `walker.run()` and `processor.run()`

### Walker Entrypoint

```python
def run(argv: list[str]) -> int:
    settings = get_settings()
    if settings.source == "filesystem":
        status, label = _run_filesystem_walk(settings)
    else:  # sharepoint
        status, label = _run_sharepoint_walk(settings)
    # log and exit
```

**SharePoint walk** (`_run_sharepoint_walk`):
1. Build `GraphClient` from `GraphSettings` (or fail if unconfigured)
2. Build `MessageQueue` from `QueueSettings`
3. Open database session from `get_sessionmaker()`
4. Construct `Walker(session, graph, queue, request)`
5. Run `walker.walk()` → returns `WalkStatus`

**Filesystem walk** (`_run_filesystem_walk`):
1. Build `MessageQueue` from `QueueSettings` (Graph is not used)
2. Open database session
3. Construct `FilesystemWalker(session, queue, root)`
4. Run `walker.walk()` → returns `WalkStatus`

### Processor Entrypoint

```python
def run(argv: list[str]) -> int:
    settings = get_settings()
    categories = parse_category_file(settings.processor.category_file)
    classifier = create_self_consistency_classifier(categories, settings)
    
    if settings.source == "filesystem":
        source = FilesystemContentSource(settings.filesystem.root)
    else:
        source = GraphContentSource(create_graph_client())
    
    queue = create_message_queue()
    session = get_sessionmaker()()
    voter = create_self_consistency_classifier(categories, settings)
    writer = DatabaseWriter(session)
    
    processor = Processor(session, source, queue, voter, writer)
    processor.run_once()
```

The processor processes **one message per invocation** (KEDA spawns one replica per message). If the queue is empty, it returns cleanly; if a message is received, it processes it and deletes it on success (or re-raises on failure so it reappears for retry).

---

## Summary: Configuration for Each Job

| Job | Requires | Optional |
|-----|----------|----------|
| **Walker (SharePoint)** | `CLASSIFIER__GRAPH_*`, `CLASSIFIER__QUEUE_*`, `CLASSIFIER__DATABASE_URL`, `CLASSIFIER__WALKER_DRIVE_ID` | `CLASSIFIER__WALKER_ROOT_PATH`, `CLASSIFIER__WALKER_TIME_BUDGET_SECONDS` |
| **Walker (Filesystem)** | `CLASSIFIER__QUEUE_*`, `CLASSIFIER__DATABASE_URL`, `CLASSIFIER__FILESYSTEM_ROOT` | — |
| **Processor** | `CLASSIFIER_PROVIDER` + its credentials, `CLASSIFIER__QUEUE_*`, `CLASSIFIER__DATABASE_URL`, `CLASSIFIER__PROCESSOR_CATEGORY_FILE`, `CLASSIFIER__GRAPH_*` (if SharePoint) or `CLASSIFIER__FILESYSTEM_ROOT` (if filesystem) | `CLASSIFIER_N`, `CLASSIFIER_TEMPERATURE` |
| **Local CLI** | Inference provider credentials | — |
| **Migrations** | `CLASSIFIER__DATABASE_URL` only | — |

Each section validates independently and resolves to `None` when wholly absent. A job that doesn't use a section simply gets `Settings.section = None`.
