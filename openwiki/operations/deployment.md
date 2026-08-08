---
type: Operations Guide
title: Container Build, CI, and OIDC Deployment
description: Docker image, GitHub Actions workflows, CI gates, OIDC federated credentials, and ACA job setup
resource: /Dockerfile, /.github/workflows/docker-build.yml, /.github/workflows/docker-publish.yml
tags: [operations, deployment, container, ci-cd, github-actions, oidc, azure]
---

# Container Build, CI, and OIDC Deployment

This page documents the containerized production pipeline: Docker build, CI workflows, OIDC federated credential setup, and Azure Container Apps (ACA) job configuration.

## Docker Image

Single image with two role-based entry points, decoupled by Azure Queue and PostgreSQL.

### Build

```bash
docker build -t classifier .
```

**Dockerfile:** [/Dockerfile](/Dockerfile)

**Key layers:**
1. Base image: Python 3.12
2. System dependencies: for PDF extraction, DOCX parsing, Azure SDK
3. Python dependencies: installed via `uv` (uses pre-built `uv.lock`)
4. Source code: `/src/` copied into image
5. Placeholder category file: `/app/categories.md` (overridable at runtime)

### Run: Walker (Producer)

```bash
docker run --rm \
  --env-file .env \
  classifier \
  python -m walker
```

**Required env vars:**
- `CLASSIFIER_PROVIDER` — `anthropic` or `foundry`
- Inference provider credentials (`ANTHROPIC_API_KEY` or Foundry settings)
- `CLASSIFIER__DATABASE_URL` — PostgreSQL connection
- `CLASSIFIER__GRAPH_*` — Microsoft Graph auth
- `CLASSIFIER__QUEUE_*` — Azure Queue auth
- `CLASSIFIER__WALKER_DRIVE_ID` — SharePoint drive ID
- `CLASSIFIER__WALKER_ROOT_PATH` (optional) — Scoped path
- `CLASSIFIER__WALKER_TIME_BUDGET_SECONDS` (optional) — Time limit

**Exit behavior:** Runs once and exits (not a long-running server). Scheduler triggers the next run.

### Run: Processor (Consumer)

```bash
docker run --rm \
  --env-file .env \
  classifier \
  python -m processor
```

**Required env vars:**
- `CLASSIFIER_PROVIDER` — `anthropic` or `foundry`
- Inference provider credentials
- `CLASSIFIER__DATABASE_URL` — PostgreSQL connection
- `CLASSIFIER__GRAPH_*` — Microsoft Graph auth
- `CLASSIFIER__QUEUE_*` — Azure Queue auth
- `CLASSIFIER__PROCESSOR_CATEGORY_FILE` — Path to category Markdown

**Exit behavior:** Runs once per message and exits. KEDA spawns one replica per queued message.

### Run: Schema Migration

```bash
docker run --rm \
  --env-file .env \
  classifier \
  alembic upgrade head
```

**Purpose:** Run once per deploy, **before** launching walker/processor jobs. Ensures schema is up-to-date.

### Local Testing (Azurite)

For local development without Azure resources:

```bash
# Terminal 1: Start Azurite (local Azure Storage emulator)
azurite --queue

# Terminal 2: Processor with Azurite queue
export CLASSIFIER__QUEUE_CONNECTION_STRING="DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXOU+FH+U7AppY4=;QueueEndpoint=http://127.0.0.1:10001/devstoreaccount1;"
docker run --rm --env-file .env classifier python -m processor
# Message not found → exits cleanly
```

## CI Workflows

**Two GitHub Actions workflows:**

### docker-build.yml (PR Gate)

**Trigger:** On every PR

**Steps:**
1. Build image
2. Run smoke tests:
   - `python -m main --help` → verify CLI entry point
   - Import all modules → verify syntax, dependencies
3. Discard image

**Purpose:** QA gate to keep `main` always-publishable. Fails the PR if build fails or smoke tests fail. Pushes nothing to registry.

**File:** [/.github/workflows/docker-build.yml](/.github/workflows/docker-build.yml)

### docker-publish.yml (Main Publish)

**Trigger:** On merge to `main`

**Steps:**
1. Build image
2. Run smoke tests (same as docker-build.yml)
3. **Skip gate:** If `ACR_LOGIN_SERVER` repo variable is not set, skip publishing (E8 not yet provisioned)
4. Log in to Azure Container Registry via OIDC
5. Push image with two tags:
   - `:latest` (floating tag)
   - `:<commit-sha>` (immutable tag)

**Purpose:** Package the image and push to production registry (Azure Container Registry). Gates on OIDC setup.

**File:** [/.github/workflows/docker-publish.yml](/.github/workflows/docker-publish.yml)

## GitHub Actions Configuration Reference

**Scope matters:**

- **Repository variables** (`ACR_LOGIN_SERVER`, `ACR_NAME`) — visible in job-level `if:` conditions; must be here for skip gate
- **Environment secrets** (`AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`) — `production` environment; requires approval; tighter access control

### Required Settings (In Settings → Secrets and variables → Actions)

#### Repository Variables

| Name | Scope | Used By | Example | Purpose |
| --- | --- | --- | --- | --- |
| `ACR_LOGIN_SERVER` | Repository | `docker-publish.yml` | `myregistry.azurecr.io` | Image registry URL; used in skip-gate and push commands |
| `ACR_NAME` | Repository | `docker-publish.yml` | `myregistry` | Registry name for `az acr login` |

#### Environment Secrets (production)

| Name | Scope | Used By | Example | Purpose |
| --- | --- | --- | --- | --- |
| `AZURE_CLIENT_ID` | `production` | `docker-publish.yml` | `00000000-0000-0000-0000-000000000000` | Federated identity client ID for OIDC login |
| `AZURE_TENANT_ID` | `production` | `docker-publish.yml` | `00000000-0000-0000-0000-000000000000` | Azure Entra tenant ID for OIDC login |
| `AZURE_SUBSCRIPTION_ID` | `production` | `docker-publish.yml` | `00000000-0000-0000-0000-000000000000` | Azure subscription ID for OIDC login |

### Skip Gate Logic

```yaml
- name: Log in to ACR
  if: vars.ACR_LOGIN_SERVER != ''  # Skip if variable not set
  uses: azure/login@v1
  with:
    client-id: ${{ secrets.AZURE_CLIENT_ID }}
    tenant-id: ${{ secrets.AZURE_TENANT_ID }}
    subscription-id: ${{ secrets.AZURE_SUBSCRIPTION_ID }}
    # ...
```

**Consequence:** If `ACR_LOGIN_SERVER` is not set (E8 not yet done), the publish job logs this and exits cleanly (no error). Once the variable is set, the job publishes.

## OIDC Federated Credentials Setup

OIDC lets GitHub Actions obtain a short-lived Azure token without storing a client secret. **Set up once.**

### Prerequisites

- Azure subscription with access to create app registrations and managed identities
- GitHub organization/repository
- `az` CLI installed and authenticated

### Step 1: Create App Registration

Register an app in Azure Entra ID that will serve as the identity for CI/CD:

```bash
az ad app create --display-name "classifier-ci"
# Returns: appId (use this as CLIENT_ID)

# Store it
export CLIENT_ID="<appId>"
```

### Step 2: Create Managed Identity (or Service Principal)

The registry needs an identity to be assigned:

```bash
az identity create \
  --resource-group <resource-group> \
  --name classifier-acr-identity

# Assign the app registration to this identity
az role assignment create \
  --assignee $CLIENT_ID \
  --role AcrPush \
  --resource-group <resource-group>
```

Alternatively, assign the app directly to the ACR:

```bash
az role assignment create \
  --assignee $CLIENT_ID \
  --role AcrPush \
  --scope /subscriptions/<subscription-id>/resourcegroups/<resource-group>/providers/Microsoft.ContainerRegistry/registries/<registry-name>
```

### Step 3: Create Federated Credential

Enable GitHub → Azure OIDC token exchange:

```bash
az ad app federated-credential create \
  --id $CLIENT_ID \
  --parameters '{
    "name": "classifier-github-actions",
    "issuer": "https://token.actions.githubusercontent.com",
    "subject": "repo:myorg/classifier:ref:refs/heads/main",
    "audiences": ["api://AzureADTokenExchange"]
  }'
```

**subject format:** `repo:<org>/<repo>:<ref>:<branch>` or `repo:<org>/<repo>:pull_request` for all PRs

**Result:** GitHub Actions can exchange its JWT for an Azure token; no client secret needed.

### Step 4: Gather Azure IDs

```bash
# Tenant ID
TENANT_ID=$(az account show --query tenantId -o tsv)

# Subscription ID
SUBSCRIPTION_ID=$(az account show --query id -o tsv)

# Client ID (from step 1)
# Already exported
```

### Step 5: Store in GitHub Secrets

In GitHub repository settings (Settings → Secrets and variables → Actions):

1. **Production Environment** → Create `production` if it doesn't exist
2. **Add secrets** to the `production` environment:
   - `AZURE_CLIENT_ID` = `$CLIENT_ID`
   - `AZURE_TENANT_ID` = `$TENANT_ID`
   - `AZURE_SUBSCRIPTION_ID` = `$SUBSCRIPTION_ID`
3. **Repository Variables** (not environment-scoped):
   - `ACR_LOGIN_SERVER` = `myregistry.azurecr.io`
   - `ACR_NAME` = `myregistry`

### Step 6: Test

Push a commit to a branch or open a PR. GitHub Actions should:

1. Build image (docker-build.yml)
2. If merging to main, also publish (docker-publish.yml):
   - Request OIDC token from GitHub
   - Exchange for Azure short-lived token via federated credential
   - Log in to ACR
   - Push image

**Logs:** Check the workflow run in GitHub Actions to see token exchange and image push.

## Azure Container Apps (ACA) Job Setup

Once the image is published to ACR, configure two ACA jobs in the Azure portal or via `az containerapp`.

### Job 1: Walker (Scheduled)

```bash
az containerapp job create \
  --name classifier-walker \
  --resource-group <resource-group> \
  --environment <aca-environment> \
  --trigger-type Schedule \
  --cron-expression "0 */6 * * *" \  # Every 6 hours
  --image $ACR_LOGIN_SERVER/classifier:latest \
  --cpu 1 --memory 2Gi \
  --command "python" \
  --args "-m" "walker" \
  --secrets-json @secrets.json \
  --env-vars-json @env-vars.json \
  --no-wait
```

**Properties:**
- **Trigger:** Schedule (cron expression)
- **Command override:** `python -m walker`
- **Secrets:** PostgreSQL URL, Graph credentials, queue credentials (from Azure Key Vault)
- **Env vars:** `CLASSIFIER_N`, `CLASSIFIER_TEMPERATURE`, etc.
- **Repeat interval:** Configure based on enumeration size and speed

### Job 2: Processor (Queue-Triggered)

```bash
az containerapp job create \
  --name classifier-processor \
  --resource-group <resource-group> \
  --environment <aca-environment> \
  --trigger-type Event \
  --parallelism 10 \  # Max 10 replicas
  --replica-completion-count 1 \  # One replica per message
  --event-scale-rule-name azure-queue \
    --event-scale-rule-type azure-queue \
    --event-scale-rule-metadata queueName=classifier-work-items \
                               connectionFromEnv=CLASSIFIER__QUEUE_CONNECTION_STRING \
                               queueLength=1 \  # Scale trigger
  --image $ACR_LOGIN_SERVER/classifier:latest \
  --cpu 1 --memory 2Gi \
  --command "python" \
  --args "-m" "processor" \
  --secrets-json @secrets.json \
  --env-vars-json @env-vars.json \
  --no-wait
```

**Properties:**
- **Trigger:** Azure Queue event (KEDA scaler)
- **Command override:** `python -m processor`
- **Parallelism:** Max replicas spawned (e.g., 10)
- **Scale rule:** One replica per message in queue (if queue length >= 1)
- **Replica exit:** Each replica exits after processing one message; ACA terminates it
- **Scale-to-zero:** When queue is empty, ACA scales to 0 replicas (no idle cost)

### Environment & Secrets

**Secrets (from Key Vault):**
- `ANTHROPIC_API_KEY` or Foundry credentials
- `CLASSIFIER__DATABASE_URL` (PostgreSQL)
- `CLASSIFIER__GRAPH_CLIENT_SECRET` (if using client credentials; preferred: managed identity)
- `CLASSIFIER__QUEUE_CONNECTION_STRING` (if using connection string; preferred: managed identity)

**Env vars:**
- `CLASSIFIER_PROVIDER` (`anthropic` or `foundry`)
- `CLASSIFIER_FOUNDRY_RESOURCE` (if using Foundry)
- `CLASSIFIER_N` (default: 5)
- `CLASSIFIER_TEMPERATURE` (default: 0.4)
- `CLASSIFIER_CONFIDENCE_THRESHOLD` (default: 0.6)
- `CLASSIFIER__GRAPH_TENANT_ID`, `CLASSIFIER__GRAPH_CLIENT_ID` (if using client credentials)
- `CLASSIFIER__WALKER_DRIVE_ID` (walker only)
- `CLASSIFIER__WALKER_ROOT_PATH` (walker only, default: `/Matters`)
- `CLASSIFIER__WALKER_TIME_BUDGET_SECONDS` (walker only, default: 600)
- `CLASSIFIER__PROCESSOR_CATEGORY_FILE` (processor only, default: `/app/categories.md`)

### Pre-Launch: Schema Migration

Before deploying walker/processor, run the schema migration **once**:

```bash
az containerapp job create \
  --name classifier-migrate \
  --resource-group <resource-group> \
  --command "alembic" \
  --args "upgrade" "head" \
  # ... secrets, env vars ...
  --no-wait

# Monitor progress
az containerapp job execution list --name classifier-migrate
```

Once successful, delete or disable this job (no longer needed until schema changes).

## Monitoring & Observability

### Logs

- **Walker logs:** ACA job logs → Application Insights or Log Analytics
- **Processor logs:** ACA job logs → Application Insights
- **PostgreSQL logs:** Azure Database for PostgreSQL server logs

### Metrics

- **Queue depth:** Azure Storage queue message count (Monitor → Metrics)
- **Processing latency:** Time from enqueue to UPSERT
- **Cost:** ProcessingLog table (token counts and calculated cost per attempt)

### Alarms

- **High poison count:** Set alarm if `error_message` contains "Poison message"
- **Graph auth failures:** Monitor for recurring GraphError
- **Queue backlog:** Alert if queue depth stays high (processor bottleneck or failures)

## Related Pages

- [Cloud Pipeline Workflow](../workflows/cloud-pipeline.md) — How walker/processor work together
- [Configuration](configuration.md) — Environment variables for all modes
- [Cloud Seams](cloud-seams.md) — Queue and Graph authentication
- [Error Handling](error-handling.md) — Retry logic and error classification
