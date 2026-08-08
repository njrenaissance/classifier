---
type: Operations Guide
title: Configuration Management
description: Environment variables, settings singleton, and tuning parameters
resource: /src/config.py
tags: [configuration, settings, environment, operations]
---

# Configuration Management

All application settings are centralized in `src/config.py` using **Pydantic Settings**, which loads configuration from environment variables and an optional `.env` file.

## Settings Singleton

```python
from config import get_settings

# Call once and reuse
settings = get_settings()  # Cached, returns same instance

# Access fields
print(settings.anthropic_api_key)
print(settings.self_consistency_n)
```

The `get_settings()` function returns a cached singleton (via `@lru_cache`), so there's only one `Settings` instance per process.

## Configuration Sources

Settings are resolved in this order (highest priority first):

1. **Environment variables**
2. **`.env` file** (in the current working directory)
3. **Field defaults** (if defined in `Settings`)

### Example `.env` File

```bash
ANTHROPIC_API_KEY=sk-ant-...
CLASSIFIER_N=5
CLASSIFIER_TEMPERATURE=0.4
CLASSIFIER_CONFIDENCE_THRESHOLD=0.6
```

## Available Settings

Settings are organized into three groups: **inference provider** (Anthropic or Foundry), **classification tuning**, and **cloud pipeline** (walker, processor, queue, Graph, database).

### Inference Provider Selection

Choose one inference provider:

#### Provider: Anthropic (Direct API)

```bash
CLASSIFIER_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-your-key-here
CLASSIFIER_ANTHROPIC_MODEL=claude-haiku-4-5  # Optional, default: claude-haiku-4-5
```

- **Env vars:**
  - `CLASSIFIER_PROVIDER` — Set to `anthropic` (default)
  - `ANTHROPIC_API_KEY` — Anthropic API key (required for Anthropic provider)
  - `CLASSIFIER_ANTHROPIC_MODEL` — Model ID override (optional)

#### Provider: Microsoft Foundry (Azure)

```bash
CLASSIFIER_PROVIDER=foundry
ANTHROPIC_FOUNDRY_RESOURCE=my-resource
# Choose ONE auth mode:
#   Option A: Managed Identity (production)
CLASSIFIER_FOUNDRY_USE_MANAGED_IDENTITY=true
#   Option B: API Key (local dev)
CLASSIFIER_FOUNDRY_USE_MANAGED_IDENTITY=false
ANTHROPIC_FOUNDRY_API_KEY=your-foundry-key-here
# Optional:
CLASSIFIER_FOUNDRY_MODEL=claude-haiku-4-5  # Default: claude-haiku-4-5
CLASSIFIER_FOUNDRY_TOKEN_SCOPE=https://cognitiveservices.azure.com/.default  # Default
```

- **Env vars:**
  - `CLASSIFIER_PROVIDER` — Set to `foundry`
  - `ANTHROPIC_FOUNDRY_RESOURCE` — Foundry resource name (e.g., `my-resource` for `https://my-resource.services.ai.azure.com/anthropic/`)
  - `CLASSIFIER_FOUNDRY_USE_MANAGED_IDENTITY` — Boolean; if true, use managed identity; if false, use API key
  - `ANTHROPIC_FOUNDRY_API_KEY` — API key (required if `use_managed_identity=false`)
  - `CLASSIFIER_FOUNDRY_MODEL` — Model ID override (optional)
  - `CLASSIFIER_FOUNDRY_TOKEN_SCOPE` — Token scope for Entra ID (optional)

### Classification Tuning (Provider-Agnostic)

These settings apply regardless of which provider is selected:

#### Self-Consistency Runs

- **Env var:** `CLASSIFIER_N`
- **Default:** `5`
- **Range:** ≥ 1
- **Type:** `int`

Number of times the classifier is called per document. Higher values increase accuracy but increase API cost and latency.

**Examples:**
```bash
CLASSIFIER_N=1      # Single-pass (no voting, confidence always 1.0 or 0.0)
CLASSIFIER_N=5      # Default (balanced accuracy/cost)
CLASSIFIER_N=10     # High accuracy (double the cost)
```

#### LLM Temperature

- **Env var:** `CLASSIFIER_TEMPERATURE`
- **Default:** `0.4`
- **Range:** `[0.0, 1.0]`
- **Type:** `float`

Temperature controls randomness in the LLM's output. Higher values increase variation across the N self-consistency runs.

**Effects:**
- **0.0:** Deterministic; all N runs return the same label (confidence = 1.0)
- **0.4:** Moderate; some variation (default)
- **1.0:** Maximum randomness; high variation across runs

**Examples:**
```bash
CLASSIFIER_TEMPERATURE=0.0   # Deterministic (useful for testing)
CLASSIFIER_TEMPERATURE=0.4   # Default (balanced)
CLASSIFIER_TEMPERATURE=0.8   # High variation
```

#### Confidence Threshold

- **Env var:** `CLASSIFIER_CONFIDENCE_THRESHOLD`
- **Default:** `0.6`
- **Range:** `[0.0, 1.0]`
- **Type:** `float`

Threshold for marking labels as `unknown`. If the modal confidence is at or below this value, the result is marked `unknown` for human review.

**Effects:**
- **0.0:** Only unanimous votes count as confident (very strict)
- **0.6:** Default (labels with < 60% agreement marked unknown)
- **1.0:** All non-unanimous votes marked unknown (all results have confidence < 1.0)

**Examples:**
```bash
CLASSIFIER_CONFIDENCE_THRESHOLD=0.5   # Stricter (more `unknown` results)
CLASSIFIER_CONFIDENCE_THRESHOLD=0.6   # Default
CLASSIFIER_CONFIDENCE_THRESHOLD=0.8   # More permissive (fewer `unknown`)
```

### Cloud Pipeline — PostgreSQL

Required by walker and processor; skipped by local CLI.

- **Env var:** `CLASSIFIER__DATABASE_URL`
- **Default:** None
- **Type:** PostgreSQL connection string (URL)
- **Required for:** Walker, processor

**Format:**
```
postgresql+psycopg://user:password@host:5432/database
```

**Example:**
```bash
CLASSIFIER__DATABASE_URL=postgresql+psycopg://classifier:pass@db.example.com:5432/classifier
```

See [state-store.md](state-store.md) for schema documentation.

### Cloud Pipeline — Microsoft Graph / SharePoint

Required by walker and processor; skipped by local CLI.

Choose **one** authentication mode:

#### Graph Auth: Managed Identity (Production)

```bash
CLASSIFIER__GRAPH_USE_MANAGED_IDENTITY=true
```

- **Env vars:**
  - `CLASSIFIER__GRAPH_USE_MANAGED_IDENTITY` — Boolean; set to `true`
  - `CLASSIFIER__GRAPH_TENANT_ID` — (optional; derived from managed identity if not set)
  - `CLASSIFIER__GRAPH_TOKEN_SCOPE` — (optional, default: `https://graph.microsoft.com/.default`)
  - `CLASSIFIER__GRAPH_BASE_URL` — (optional, default: `https://graph.microsoft.com/v1.0`)

#### Graph Auth: Client Credentials (Local Dev)

```bash
CLASSIFIER__GRAPH_USE_MANAGED_IDENTITY=false
CLASSIFIER__GRAPH_TENANT_ID=00000000-0000-0000-0000-000000000000
CLASSIFIER__GRAPH_CLIENT_ID=00000000-0000-0000-0000-000000000000
CLASSIFIER__GRAPH_CLIENT_SECRET=your-secret-here
```

- **Env vars:**
  - `CLASSIFIER__GRAPH_USE_MANAGED_IDENTITY` — Boolean; set to `false`
  - `CLASSIFIER__GRAPH_TENANT_ID` — Azure Entra tenant ID
  - `CLASSIFIER__GRAPH_CLIENT_ID` — App registration client ID
  - `CLASSIFIER__GRAPH_CLIENT_SECRET` — App registration client secret
  - `CLASSIFIER__GRAPH_TOKEN_SCOPE` — (optional)
  - `CLASSIFIER__GRAPH_BASE_URL` — (optional)

### Cloud Pipeline — Azure Queue

Required by walker and processor; skipped by local CLI.

Choose **one** authentication mode:

#### Queue Auth: Connection String (Local Dev / Azurite)

```bash
CLASSIFIER__QUEUE_CONNECTION_STRING=DefaultEndpointsProtocol=https;AccountName=...;AccountKey=...
CLASSIFIER__QUEUE_NAME=classifier-work-items
```

- **Env vars:**
  - `CLASSIFIER__QUEUE_CONNECTION_STRING` — Azure Storage connection string
  - `CLASSIFIER__QUEUE_NAME` — Queue name

#### Queue Auth: Managed Identity (Production)

```bash
CLASSIFIER__QUEUE_USE_MANAGED_IDENTITY=true
CLASSIFIER__QUEUE_ACCOUNT_URL=https://your-account.queue.core.windows.net
CLASSIFIER__QUEUE_NAME=classifier-work-items
```

- **Env vars:**
  - `CLASSIFIER__QUEUE_USE_MANAGED_IDENTITY` — Boolean; set to `true`
  - `CLASSIFIER__QUEUE_ACCOUNT_URL` — Storage account queue endpoint
  - `CLASSIFIER__QUEUE_NAME` — Queue name

### Cloud Pipeline — Walker (Scheduled Job)

Required by walker only; ignored by processor and local CLI.

```bash
CLASSIFIER__WALKER_DRIVE_ID=your-sharepoint-drive-id
CLASSIFIER__WALKER_ROOT_PATH=/Matters                 # Optional, default: /Matters
CLASSIFIER__WALKER_TIME_BUDGET_SECONDS=600            # Optional, default: 600 (10 min)
```

- **Env vars:**
  - `CLASSIFIER__WALKER_DRIVE_ID` — SharePoint drive ID (required)
  - `CLASSIFIER__WALKER_ROOT_PATH` — Library subtree to enumerate (optional, default: `/Matters`). Set to `/` to walk entire library.
  - `CLASSIFIER__WALKER_TIME_BUDGET_SECONDS` — Time limit per run in seconds (optional, default: 600)

### Cloud Pipeline — Processor (Queue-Triggered Job)

Required by processor only; ignored by walker and local CLI.

```bash
CLASSIFIER__PROCESSOR_CATEGORY_FILE=categories.md
```

- **Env vars:**
  - `CLASSIFIER__PROCESSOR_CATEGORY_FILE` — Path to category Markdown file (required)

## The Settings Class

The settings are implemented as a hierarchy of Pydantic models in `src/config.py`:

```python
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

class InferenceProviderSettings(BaseModel):
    """Shared inference provider config (Anthropic or Foundry)."""
    provider: str = Field(default="anthropic", validation_alias="CLASSIFIER_PROVIDER")
    # ... provider-specific fields ...

class AnthropicSettings(BaseModel):
    """Anthropic-specific config."""
    api_key: SecretStr = Field(validation_alias="ANTHROPIC_API_KEY")
    model: str = Field(default="claude-haiku-4-5", validation_alias="CLASSIFIER_ANTHROPIC_MODEL")

class FoundrySettings(BaseModel):
    """Microsoft Foundry-specific config."""
    resource: str = Field(validation_alias="ANTHROPIC_FOUNDRY_RESOURCE")
    use_managed_identity: bool = Field(default=False, validation_alias="CLASSIFIER_FOUNDRY_USE_MANAGED_IDENTITY")
    api_key: SecretStr | None = Field(default=None, validation_alias="ANTHROPIC_FOUNDRY_API_KEY")
    # ... token scope, model, etc. ...

class GraphSettings(BaseModel):
    """Microsoft Graph / SharePoint config."""
    use_managed_identity: bool = Field(default=False, validation_alias="CLASSIFIER__GRAPH_USE_MANAGED_IDENTITY")
    tenant_id: str | None = Field(default=None, validation_alias="CLASSIFIER__GRAPH_TENANT_ID")
    client_id: str | None = Field(default=None, validation_alias="CLASSIFIER__GRAPH_CLIENT_ID")
    client_secret: SecretStr | None = Field(default=None, validation_alias="CLASSIFIER__GRAPH_CLIENT_SECRET")
    # ... token scope, base URL ...

class QueueSettings(BaseModel):
    """Azure Queue Storage config."""
    name: str = Field(validation_alias="CLASSIFIER__QUEUE_NAME")
    connection_string: str | None = Field(default=None, validation_alias="CLASSIFIER__QUEUE_CONNECTION_STRING")
    use_managed_identity: bool = Field(default=False, validation_alias="CLASSIFIER__QUEUE_USE_MANAGED_IDENTITY")
    account_url: str | None = Field(default=None, validation_alias="CLASSIFIER__QUEUE_ACCOUNT_URL")

class WalkerSettings(BaseModel):
    """Walker job config."""
    drive_id: str = Field(validation_alias="CLASSIFIER__WALKER_DRIVE_ID")
    root_path: str = Field(default="/Matters", validation_alias="CLASSIFIER__WALKER_ROOT_PATH")
    time_budget_seconds: int = Field(default=600, validation_alias="CLASSIFIER__WALKER_TIME_BUDGET_SECONDS")

class ProcessorSettings(BaseModel):
    """Processor job config."""
    category_file: str = Field(validation_alias="CLASSIFIER__PROCESSOR_CATEGORY_FILE")

class Settings(BaseSettings):
    """Main application settings."""
    
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    
    # Inference provider
    provider: str = Field(default="anthropic", validation_alias="CLASSIFIER_PROVIDER")
    anthropic: AnthropicSettings = Field(default_factory=AnthropicSettings)
    foundry: FoundrySettings = Field(default_factory=FoundrySettings)
    
    # Classification tuning (provider-agnostic)
    self_consistency_n: int = Field(default=5, ge=1, validation_alias="CLASSIFIER_N")
    temperature: float = Field(default=0.4, ge=0.0, le=1.0, validation_alias="CLASSIFIER_TEMPERATURE")
    confidence_threshold: float = Field(default=0.6, ge=0.0, le=1.0, validation_alias="CLASSIFIER_CONFIDENCE_THRESHOLD")
    
    # Cloud pipeline
    database_url: str | None = Field(default=None, validation_alias="CLASSIFIER__DATABASE_URL")
    graph: GraphSettings = Field(default_factory=GraphSettings)
    queue: QueueSettings = Field(default_factory=QueueSettings)
    walker: WalkerSettings | None = Field(default=None)
    processor: ProcessorSettings | None = Field(default=None)

@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Get or create cached settings singleton."""
    return Settings()
```

**Key details:**
- **`validation_alias`:** Maps fields to environment variable names
- **Field validation:** `ge`, `le` enforce ranges; custom validators check auth modes
- **`extra="ignore"`:** Unknown env vars don't cause errors
- **`.env` support:** Pydantic automatically reads `.env` file
- **Hierarchy:** Nested Pydantic models organize settings by functional area (provider, Graph, queue, etc.)
- **Lazy validation:** Auth mode validation happens at startup (e.g., "must have either managed identity or both client ID and secret")

## Validation & Errors

Pydantic validates all settings on load:

```python
from pydantic import ValidationError
from config import Settings

try:
    # This will fail if CLASSIFIER_TEMPERATURE is not in [0.0, 1.0]
    settings = Settings()
except ValidationError as e:
    print(f"Configuration error: {e}")
    # Example error message:
    # "1 validation error for Settings
    #  temperature
    #    Input should be a valid number, less than or equal to 1 [type=less_than_equal, ...]"
```

## Integration Points

### Classifier Core & Self-Consistency

```python
from classifier import create_classifier
from self_consistency import create_self_consistency_classifier
from config import get_settings

settings = get_settings()

# Temperature passed to classifier
classifier = create_classifier(categories, settings)

# N and threshold passed to voter
sc_classifier = create_self_consistency_classifier(
    classifier,
    n=settings.self_consistency_n,
    confidence_threshold=settings.confidence_threshold
)
```

### Inference Provider Client

```python
from config import get_settings

settings = get_settings()

if settings.provider == "anthropic":
    import anthropic
    client = anthropic.Anthropic(
        api_key=settings.anthropic.api_key.get_secret_value()
    )
elif settings.provider == "foundry":
    from anthropic import Anthropic
    # Microsoft Foundry setup
    client = Anthropic(
        api_key=settings.foundry.api_key.get_secret_value() if not settings.foundry.use_managed_identity else None,
        # ... Foundry URL and auth ...
    )
```

### Graph Client (Cloud Only)

```python
from graph_client import create_graph_client
from config import get_settings

settings = get_settings()

if settings.graph:  # Only if cloud pipeline settings present
    graph_client = create_graph_client(settings.graph)
    # Used by walker and processor
```

### Message Queue (Cloud Only)

```python
from message_queue import create_message_queue
from config import get_settings

settings = get_settings()

if settings.queue:  # Only if cloud pipeline settings present
    queue = create_message_queue(settings.queue)
    # Used by walker and processor
```

### Database (Cloud Only)

```python
from db import get_sessionmaker
from config import get_settings

settings = get_settings()

if settings.database_url:  # Only if cloud pipeline settings present
    sessionmaker = get_sessionmaker()
    # Used by walker and processor
```

## Testing & Fixtures

In tests, you can provide custom `Settings`:

```python
from config import Settings
from self_consistency import create_self_consistency_classifier

# Create custom settings for testing
test_settings = Settings(
    anthropic_api_key="test-key-123",
    self_consistency_n=2,  # Fewer runs for faster tests
    temperature=0.4,
    confidence_threshold=0.6
)

# Pass to factory functions
sc_classifier = create_self_consistency_classifier(categories, test_settings)
```

Or use a fixture in pytest:

```python
import pytest
from config import Settings

@pytest.fixture
def settings():
    return Settings(
        anthropic_api_key="test-key",
        self_consistency_n=2,
        temperature=0.0,  # Deterministic for testing
        confidence_threshold=0.6
    )

def test_classification(settings):
    sc_classifier = create_self_consistency_classifier(categories, settings)
    # ...
```

## Deployment Notes

### Local CLI (csv-out)

Minimal setup; only inference provider + tuning:

```bash
# .env file for local CLI
CLASSIFIER_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-your-key-here
CLASSIFIER_N=2                    # Faster for local testing
CLASSIFIER_TEMPERATURE=0.0         # Deterministic
CLASSIFIER_CONFIDENCE_THRESHOLD=0.5
```

### Cloud Pipeline

All three groups (provider, tuning, cloud) required:

```bash
# Inference provider
CLASSIFIER_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-your-key-here

# Classification tuning
CLASSIFIER_N=5
CLASSIFIER_TEMPERATURE=0.4
CLASSIFIER_CONFIDENCE_THRESHOLD=0.6

# Cloud: PostgreSQL
CLASSIFIER__DATABASE_URL=postgresql+psycopg://user:password@host:5432/classifier

# Cloud: Graph (choose one auth mode)
CLASSIFIER__GRAPH_USE_MANAGED_IDENTITY=false
CLASSIFIER__GRAPH_TENANT_ID=...
CLASSIFIER__GRAPH_CLIENT_ID=...
CLASSIFIER__GRAPH_CLIENT_SECRET=...

# Cloud: Queue (choose one auth mode)
CLASSIFIER__QUEUE_NAME=classifier-work-items
CLASSIFIER__QUEUE_CONNECTION_STRING=...

# Cloud: Walker job
CLASSIFIER__WALKER_DRIVE_ID=b!...
CLASSIFIER__WALKER_ROOT_PATH=/Matters
CLASSIFIER__WALKER_TIME_BUDGET_SECONDS=600

# Cloud: Processor job
CLASSIFIER__PROCESSOR_CATEGORY_FILE=/app/categories.md
```

### Environment-Specific Tuning

- **Local development (fast):**
  ```bash
  CLASSIFIER_N=2
  CLASSIFIER_TEMPERATURE=0.0  # Deterministic
  CLASSIFIER_CONFIDENCE_THRESHOLD=0.5
  ```

- **Production (high accuracy):**
  ```bash
  CLASSIFIER_N=5
  CLASSIFIER_TEMPERATURE=0.4
  CLASSIFIER_CONFIDENCE_THRESHOLD=0.6
  ```

- **Production (cost-optimized):**
  ```bash
  CLASSIFIER_N=3
  CLASSIFIER_TEMPERATURE=0.2
  CLASSIFIER_CONFIDENCE_THRESHOLD=0.7
  ```

## Secrets Management

**Important:** Never commit `.env` files with real API keys or secrets to version control.

**Secure setup — Local:**
1. Create `.env` locally (added to `.gitignore`)
2. Source it before running: `source .env && python src/main.py ...`

**Secure setup — CI/CD (GitHub Actions):**
1. Store secrets in GitHub repository settings:
   - Repository secrets: `ANTHROPIC_API_KEY`, database URLs, etc.
   - Environment secrets (`production`): Azure credentials for OIDC
2. Reference in workflows: `${{ secrets.ANTHROPIC_API_KEY }}`
3. Use the same Pydantic code (Settings reads both files and env vars)

**Secure setup — Container (ACA):**
1. Store secrets in Azure Key Vault
2. Mount as env vars on the ACA job
3. Use managed identity for Graph/queue/database auth (no secrets stored)

See [error-handling.md](error-handling.md) for how configuration validation errors are handled and [deployment.md](deployment.md) for CI/CD setup.
