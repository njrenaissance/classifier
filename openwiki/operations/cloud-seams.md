---
type: Operations Guide
title: Cloud Boundaries — Queue and Graph
description: Message queue abstraction, Microsoft Graph client, and their authentication modes
resource: /src/message_queue.py, /src/graph_client.py
tags: [operations, integration, azure, queue, graph, authentication]
---

# Cloud Boundaries — Message Queue and Graph Client

This page documents the two external system boundaries in the cloud pipeline: the Azure Queue (walker→processor decoupling) and the Microsoft Graph client (SharePoint enumeration and download).

## Message Queue (Walker→Processor Boundary)

**Technology:** Azure Queue Storage

**Abstraction:** `MessageQueue` class with `QueueBackend` protocol for testability

**Purpose:** Decouple the walker (producer) from processors (consumers); enables at-least-once delivery semantics and KEDA scaling.

### Message Wire Format

Messages are Pydantic `Message` objects serialized to JSON and placed on the Azure Queue:

```python
class Message(BaseModel):
    drive_item_id: str     # OneDrive item ID (identifier)
    file_name: str         # e.g., "invoice.pdf"
    folder_path: str       # e.g., "/Matters/Smith-2026-001"
    mime_type: str         # e.g., "application/pdf"
    content_hash: str      # SHA-256 of file bytes (idempotency guard)
```

**Serialization:** Pydantic `model_dump_json()` (JSON string) before sending; `Message.model_validate_json()` after receiving.

### Send (Walker→Queue)

```python
mq = create_message_queue(settings)
msg = Message(
    drive_item_id="...",
    file_name="invoice.pdf",
    folder_path="/Matters/Smith-2026-001",
    mime_type="application/pdf",
    content_hash="abc123..."
)
mq.enqueue(msg)
```

**Under the hood:**
1. Serialize `msg` to JSON string
2. Call `QueueBackend.send_message(json_string)`
3. Translate `AzureError` → `QueueError` (chained exception)

**Idempotency:** No de-duplication in queue itself; processor re-checks `content_hash` before download to skip unchanged files.

### Receive (Queue→Processor)

```python
received = mq.receive()
if received:
    msg = received.message  # Parsed Message object
    dequeue_count = received.dequeue_count  # Azure's redelivery counter
    # ... classify ...
    mq.delete(received.message_id, received.pop_receipt)
```

**Return value:** `ReceivedMessage | None`

```python
@dataclass(frozen=True)
class ReceivedMessage:
    message: Message           # Parsed work item
    message_id: str            # Handle for delete
    pop_receipt: str           # Handle for delete
    dequeue_count: int         # Azure redelivery counter (1, 2, 3, ...)
```

**Visibility timeout:** Processor's message is invisible to other processors for a configurable duration (default ~30 seconds). If processor crashes, message becomes visible and Azure re-delivers.

### Delete (Processor→Queue)

```python
mq.delete(received.message_id, received.pop_receipt)
```

**Semantics:** Message is removed from queue only on successful classification. If processor fails, message is not deleted and Azure redelivers it after visibility timeout.

### Dequeue Count & Poison Shedding

Azure's `dequeue_count` field tracks how many times a message has been redelivered:

```python
received = mq.receive()
if received.dequeue_count > POISON_THRESHOLD:  # e.g., 5
    # Mark document failed, do not retry
    doc.status = DocumentStatus.failed
    doc.error_message = f"Poison message (dequeue_count={received.dequeue_count})"
    # DO NOT delete message; let it expire after max redelivery count
    raise PoisonMessageError(...)
```

**Invariant:** If a message fails repeatedly (same document, same error), processor stops re-processing it after `dequeue_count` exceeds threshold. Operator must investigate and intervene manually.

**Design decision:** [ADR-0014](../../spec/adr/0014-sharepoint-delta-walker.md) (poison message handling)

### Protocol & Testability

The `QueueBackend` protocol defines the minimal surface needed by `MessageQueue`:

```python
class QueueBackend(Protocol):
    def send_message(self, content: str) -> Any: ...
    def receive_message(self, *, visibility_timeout: int | None = None) -> Any | None: ...
    def delete_message(self, message: str, pop_receipt: str) -> None: ...
    def close(self) -> None: ...
```

**Benefit:** Unit tests inject a mock `QueueBackend` without touching Azure Storage. The network boundary is completely fakeable.

**Real implementation:** `create_message_queue()` factory builds a live `azure.storage.queue.QueueClient` and wraps it in `MessageQueue`:

```python
def create_message_queue(settings: QueueSettings) -> MessageQueue:
    """Create a real Azure Queue client, wrapped for dependency injection."""
    # Auth: connection string OR (account URL + managed identity)
    if settings.connection_string:
        backend = QueueClient.from_connection_string(
            settings.connection_string,
            queue_name=settings.queue_name
        )
    else:
        backend = QueueClient(
            account_url=settings.account_url,
            queue_name=settings.queue_name,
            credential=...  # Managed identity or SAS token
        )
    return MessageQueue(backend)
```

### Error Handling

All Azure and serialization failures are translated to `QueueError`:

```python
try:
    backend.send_message(json_string)
except AzureError as e:
    raise QueueError("Failed to enqueue message") from e
except ValidationError as e:
    raise QueueError("Invalid message format") from e
```

**Consequence:** Caller catches one exception type (`QueueError`) instead of raw Azure exceptions.

## Microsoft Graph Client (SharePoint Integration)

**Purpose:** 
1. **Walker:** Delta-walk SharePoint library to enumerate changed/new files
2. **Processor:** Download file bytes for extraction

**Abstraction:** `GraphClient` class with injected auth credentials for testability

### Authentication Modes

Choose one authentication mode at startup:

#### Mode 1: Client Credentials (Local Development)

```bash
CLASSIFIER__GRAPH_USE_MANAGED_IDENTITY=false
CLASSIFIER__GRAPH_TENANT_ID=00000000-0000-0000-0000-000000000000
CLASSIFIER__GRAPH_CLIENT_ID=00000000-0000-0000-0000-000000000000
CLASSIFIER__GRAPH_CLIENT_SECRET=your-secret-here
```

**Flow:** Client credentials → OAuth token → Bearer header on Graph requests

**Tool:** Register an app in Azure Entra ID, grant `Sites.Read.All` permission (app role, not delegated)

**Design decision:** [ADR-0007](../../spec/adr/0007-sharepoint-app-only-auth.md)

#### Mode 2: Managed Identity (Production)

```bash
CLASSIFIER__GRAPH_USE_MANAGED_IDENTITY=true
```

**Flow:** Azure AD → short-lived token → Bearer header on Graph requests

**Tool:** Assign managed identity to ACA container, add identity as app to the SharePoint admin center

**Advantage:** No secrets stored; token is temporary and rotated by Azure

**Design decision:** [ADR-0007](../../spec/adr/0007-sharepoint-app-only-auth.md)

### Factory & Lazy Initialization

```python
def create_graph_client(settings: GraphSettings) -> GraphClient:
    """Create and return a configured Graph client."""
    if settings.use_managed_identity:
        credential = ManagedIdentityCredential(scopes=[settings.token_scope])
    else:
        credential = ClientSecretCredential(
            tenant_id=settings.tenant_id,
            client_id=settings.client_id,
            client_secret=settings.client_secret,
            scopes=[settings.token_scope]
        )
    return GraphClient(credential, base_url=settings.base_url)
```

**Token caching:** Azure SDK caches tokens internally; no manual cache needed.

### Walker: Delta Walk

**Entry point:** `GraphClient.list_changes(drive_id, skip_token=None, delta_token=None, root_path="/")`

**Three cases:**

1. **First enumeration (no tokens):**
   ```python
   items = client.list_changes(drive_id)
   # Returns all items in /drive
   ```

2. **Resume interrupted walk (skip_token):**
   ```python
   items = client.list_changes(drive_id, skip_token=resume_token)
   # Continues pagination from where the previous run left off
   ```

3. **Incremental walk (delta_token):**
   ```python
   items = client.list_changes(drive_id, delta_token=delta_token)
   # Returns only items changed since last complete enumeration
   ```

**Returns:** Iterator of `DriveItem` objects, plus `next_link` (pagination) and `delta_link` (completion).

**Scoping:** Graph query includes `root_path` filter, so walker sees only in-scope items:
```graphql
GET /drives/{id}/root:/{root_path}:/delta
```

Result: SharePoint item enumeration is filtered at the API level, not post-walk.

**Design decision:** [ADR-0019](../../spec/adr/0019-config-driven-walk-scope.md)

### Processor: Download

**Entry point:** `GraphClient.download(drive_item_id) -> bytes`

**Flow:**
1. Fetch lightweight metadata (to re-check content hash)
2. Download file bytes

**Content hash verification:**
```python
item = client.get_item(drive_item_id)  # Fetch metadata
graph_hash = item.get("file", {}).get("hashes", {}).get("sha256Hash")
if graph_hash != queued_message.content_hash:
    # File changed while queued; skip and let walker re-enqueue
    return
bytes_data = client.download(drive_item_id)
```

**Design decision:** [ADR-0015](../../spec/adr/0015-graph-authenticated-download.md), [ADR-0017](../../spec/adr/0017-graph-content-hash-field.md)

### API Surface

**Key methods:**

```python
class GraphClient:
    def list_changes(
        self,
        drive_id: str,
        skip_token: str | None = None,
        delta_token: str | None = None,
        root_path: str = "/"
    ) -> tuple[Iterator[DriveItem], str | None, str | None]:
        """Enumerate changed items. Returns (items, next_link, delta_link)."""
        
    def get_item(self, drive_item_id: str) -> dict[str, Any]:
        """Fetch item metadata (including content hash)."""
        
    def download(self, drive_item_id: str) -> bytes:
        """Download file bytes."""
        
    def content_hash(item: dict[str, Any]) -> str | None:
        """Extract SHA-256 hash from item metadata."""
        
    def folder_path(item: dict[str, Any]) -> str:
        """Extract folder path from item metadata."""
```

### Error Handling

All Graph failures are translated to `GraphError`:

```python
try:
    items = client.list_changes(drive_id)
except azure.core.exceptions.AzureError as e:
    raise GraphError("Failed to enumerate SharePoint") from e
except Exception as e:
    raise GraphError("Graph API error") from e
```

**Consequence:** Caller catches `GraphError` instead of raw Azure exceptions.

**Common errors:**
- Auth failure (invalid secret, revoked managed identity)
- Item not found (file was deleted while queued)
- Network timeout
- SharePoint rate limiting

### Design Decisions

- [ADR-0007](../../spec/adr/0007-sharepoint-app-only-auth.md) — App-only (not delegated) auth
- [ADR-0014](../../spec/adr/0014-sharepoint-delta-walker.md) — Delta walk for efficient enumeration
- [ADR-0015](../../spec/adr/0015-graph-authenticated-download.md) — Graph-authenticated download (avoids separate auth)
- [ADR-0017](../../spec/adr/0017-graph-content-hash-field.md) — Use Graph content hash for idempotency

## Configuration Reference

### Queue Settings

| Env Var | Type | Required | Default | Purpose |
| --- | --- | --- | --- | --- |
| `CLASSIFIER__QUEUE_NAME` | str | Yes | — | Queue name in Azure Storage |
| `CLASSIFIER__QUEUE_CONNECTION_STRING` | str | Conditional | — | Connection string (local dev / Azurite) |
| `CLASSIFIER__QUEUE_USE_MANAGED_IDENTITY` | bool | Conditional | false | Use managed identity (production) |
| `CLASSIFIER__QUEUE_ACCOUNT_URL` | str | Conditional | — | Storage account URL (with managed identity) |

**Authentication logic:**
- If `connection_string` is provided: use it
- Else if `account_url` + `use_managed_identity=true`: use managed identity
- Else: fail at startup

### Graph Settings

| Env Var | Type | Required | Default | Purpose |
| --- | --- | --- | --- | --- |
| `CLASSIFIER__GRAPH_USE_MANAGED_IDENTITY` | bool | No | false | Use managed identity (production) |
| `CLASSIFIER__GRAPH_TENANT_ID` | str | Conditional | — | Azure Entra tenant ID (client credentials only) |
| `CLASSIFIER__GRAPH_CLIENT_ID` | str | Conditional | — | App registration client ID |
| `CLASSIFIER__GRAPH_CLIENT_SECRET` | str | Conditional | — | App registration client secret |
| `CLASSIFIER__GRAPH_TOKEN_SCOPE` | str | No | `https://graph.microsoft.com/.default` | Token scope |
| `CLASSIFIER__GRAPH_BASE_URL` | str | No | `https://graph.microsoft.com/v1.0` | Graph API endpoint |

**Authentication logic:**
- If `use_managed_identity=true`: use managed identity
- Else if `tenant_id` + `client_id` + `client_secret`: use client credentials
- Else: fail at startup

See [configuration.md](configuration.md) for examples.

## Testing & Mocking

**Unit tests inject mocks:**

```python
# Mock QueueBackend
class FakeQueueBackend:
    def send_message(self, content: str) -> Any:
        self.messages.append(content)
        return {"id": "123"}
    # ...

# Mock GraphClient
class FakeGraphClient:
    def list_changes(self, drive_id, ...):
        return iter([...]), None, None
    # ...

# Inject mocks into walker/processor
walker = Walker(
    sync_state=sync_state,
    classifier=classifier,
    graph=FakeGraphClient(),
    queue=FakeQueueBackend(),
    session=session,
    clock=FakeClock()
)
```

**Result:** No network calls, no Azure resources, tests run in milliseconds.

## Related Pages

- [Cloud Pipeline Workflow](../workflows/cloud-pipeline.md) — How queue and Graph are used
- [State Store](state-store.md) — Document and SyncState database models
- [Configuration](configuration.md) — Environment variables for queue and Graph
- [Error Handling](error-handling.md) — Error types and handling strategies
