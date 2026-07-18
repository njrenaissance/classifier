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

### Anthropic API Key (Required)

```python
anthropic_api_key: SecretStr  # From ANTHROPIC_API_KEY
```

- **Env var:** `ANTHROPIC_API_KEY` (unprefixed)
- **Required:** Yes
- **Default:** None
- **Type:** `SecretStr` (masked in logs/repr)

**Setup:**
```bash
export ANTHROPIC_API_KEY="sk-ant-..."
# Or add to .env file
```

### Self-Consistency Runs

```python
self_consistency_n: int  # From CLASSIFIER_N
```

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

### LLM Temperature

```python
temperature: float  # From CLASSIFIER_TEMPERATURE
```

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

### Confidence Threshold

```python
confidence_threshold: float  # From CLASSIFIER_CONFIDENCE_THRESHOLD
```

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

## The Settings Class

```python
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    """Application settings from environment and .env file."""
    
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    
    anthropic_api_key: SecretStr = Field(validation_alias="ANTHROPIC_API_KEY")
    self_consistency_n: int = Field(default=5, ge=1, validation_alias="CLASSIFIER_N")
    temperature: float = Field(default=0.4, ge=0.0, le=1.0, validation_alias="CLASSIFIER_TEMPERATURE")
    confidence_threshold: float = Field(default=0.6, ge=0.0, le=1.0, validation_alias="CLASSIFIER_CONFIDENCE_THRESHOLD")
```

**Key details:**
- **`validation_alias`:** Maps the field to the env var name
- **Field validation:** `ge` and `le` enforce ranges (≥ and ≤)
- **`extra="ignore"`:** Unknown env vars don't cause errors
- **`.env` support:** Pydantic automatically reads `.env` file

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

### Classifier Core (B1)

```python
from classifier import create_classifier
from config import get_settings

settings = get_settings()
classifier = create_classifier(categories, settings)
# Passes temperature to the Classifier constructor
```

### Self-Consistency (B2)

```python
from self_consistency import create_self_consistency_classifier
from config import get_settings

settings = get_settings()
sc_classifier = create_self_consistency_classifier(categories, settings)
# Passes self_consistency_n and confidence_threshold
```

### Anthropic Client

```python
import anthropic
from config import get_settings

settings = get_settings()
client = anthropic.Anthropic(api_key=settings.anthropic_api_key.get_secret_value())
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

**Environment-specific tuning:**

- **Local development:**
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

**Important:** Never commit `.env` files with real API keys to version control.

**Secure setup:**
1. Create `.env` locally (added to `.gitignore`)
2. In CI/CD, set `ANTHROPIC_API_KEY` as a secret environment variable
3. Use the same configuration loading code (Pydantic reads both)

See [../operations/error-handling.md](error-handling.md) for how configuration errors are handled.
