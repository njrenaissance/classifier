# classifier

Generated from the `basic` cookiecutter template.
A minimal Python project, managed with [uv](https://docs.astral.sh/uv/).

## Structure

```bash
├── src/
│   └── main.py       # CLI entry point (local end-to-end)
├── tests/
│   └── test_main.py  # tests for the CLI
└── pyproject.toml
```

## Setup

This project uses `uv` for package management, linting, and formatting.

```bash
uv sync
```

## Wiki

This project keeps an `openwiki/` folder of generated codebase documentation
(produced by [OpenWiki](https://www.npmjs.com/package/openwiki)). It is
generated output — **never hand-edit it**. A scheduled CI workflow
(`.github/workflows/openwiki-update.yml`) keeps `openwiki/` current
automatically: it regenerates the wiki daily on the Anthropic provider and opens
a single rolling review PR — no auto-merge (see
[ADR-0011](spec/adr/0011-openwiki-ci-regeneration.md)). OpenWiki records its own
provenance in `openwiki/.last-update.json` (`updatedAt`, `gitHead`, `model`), so
you can tell how current the wiki is and which commit it was built from.

OpenWiki is a per-machine global CLI, **not** a project dependency (it is never
added to `pyproject.toml`). Regenerating in the same PR as a code change is
optional now that CI is the primary path, but if you want the docs updated
immediately, install and authenticate the CLI once, then regenerate:

```bash
npm install -g openwiki    # one-time, per machine
openwiki auth <provider>   # one-time: sets up the LLM provider + API key
openwiki code --init       # first run in a fresh repo
openwiki code --update     # regenerate on demand
```

Regenerating calls a paid LLM provider. See `.claude/standards/wiki.md` for the
rules agents follow.

## Run

Point the CLI at a local source (a file or a directory) plus a category
Markdown file; it writes a CSV of `filename,category,confidence`:

```bash
export ANTHROPIC_API_KEY=sk-...   # required
uv run python src/main.py ./docs -c categories.md -o results.csv
```

`source` is positional; `-c/--categories` and `-o/--output` are required.
Unsupported files in a directory are skipped with a warning, and a file that
fails extraction or classification is skipped so the rest of the batch still
completes.

## Container (production)

The production pipeline ships as **one image with two entry points** (ADR-0012),
decoupled by an Azure Storage queue. The same image runs in two roles; the Azure
Container Apps (ACA) job `command` override selects which process runs.

| Role | Command | ACA job type | What one run does |
| --- | --- | --- | --- |
| **Producer** (queue manager) | `python -m walker` | **Scheduled** (cron) | Runs a time-budgeted Microsoft Graph delta walk of the SharePoint library, tracks its position in PostgreSQL, and **enqueues one message per changed/new file**. Resumable across runs. |
| **Consumer** (worker) | `python -m processor` | **Queue-triggered** (KEDA `azure-queue` scaler) | Handles **one** queued message: download the file via Graph → extract text → classify (self-consistency) → UPSERT the result to PostgreSQL, then exit. KEDA spawns one replica per queued message and scales to zero when the queue drains. |

Neither job is a long-running server: the walker runs once per schedule and exits;
the processor runs once per message and exits. There is no polling loop in the code —
the schedule drives the producer and KEDA drives the consumer.

```bash
docker build -t classifier .

# Producer / queue manager — enumerate SharePoint and enqueue work
docker run --rm --env-file .env classifier python -m walker

# Consumer / worker — process one queued document
docker run --rm --env-file .env classifier python -m processor

# Schema migration — run once per deploy, before the jobs (ADR-0013)
docker run --rm --env-file .env classifier alembic upgrade head
```

Locally, `python -m walker` needs the Graph, queue, and database settings; `python
-m processor` needs the queue, Graph, database, and `CLASSIFIER__PROCESSOR_CATEGORY_FILE`.
A local run of the processor with no queued message simply finds nothing to do and
exits — point `CLASSIFIER__QUEUE_CONNECTION_STRING` at [Azurite](https://github.com/Azure/Azurite)
to exercise the producer→consumer seam without real Azure resources.

Runtime configuration is supplied via environment variables — see
[`.env.example`](.env.example) for the full set (PostgreSQL URL, Microsoft Graph
credentials, the work queue, and the walker/processor knobs). The image bakes in
a **placeholder** `categories.md`; the production category definitions are
authored separately (issue #42) and override `CLASSIFIER__PROCESSOR_CATEGORY_FILE`.

### CI: build gate and publish

Two workflows package the image:

- **`docker-build.yml`** — runs on every PR (via `ci.yml`). Builds the image and
  runs `--help`/import smoke checks, then discards it. A pre-merge **QA gate** that
  keeps `main` always-publishable; it pushes nothing.
- **`docker-publish.yml`** — runs on merge to `main`. Builds, re-runs the smoke
  checks, and **pushes** the image to Azure Container Registry (ACR) tagged with the
  commit SHA and `latest`. The ACA jobs pull from ACR directly via managed identity
  (no GHCR hop).

Publishing authenticates with **OIDC** (a federated credential — no stored registry
password) and is **gated on E8 (#46)**: the job is skipped until the
`ACR_LOGIN_SERVER` repository variable is set, and activates automatically once E8
provisions the registry + identity and sets the values below.

#### GitHub Actions configuration reference

These are the **only** repository-level settings the workflows need (everything the
container needs at *runtime* is separate — that lives in `.env` / ACA config, not
here). Set them under *Settings → Secrets and variables → Actions*. `docker-build.yml`
needs none of these; they are all consumed by `docker-publish.yml`.

Scope matters: the registry URL/name must be **repository variables** because the
publish job reads `ACR_LOGIN_SERVER` in its job-level `if:` skip-gate, and
environment-scoped values are not visible there. The Azure ids are only used inside
steps, so they live as **`production` environment secrets** for a tighter blast
radius (and a place to add a required-reviewer approval later).

| Name | Kind | Scope | Used for | Example |
| --- | --- | --- | --- | --- |
| `ACR_LOGIN_SERVER` | Variable | Repository | Image ref + publish skip-gate | `myregistry.azurecr.io` |
| `ACR_NAME` | Variable | Repository | `az acr login --name` | `myregistry` |
| `AZURE_CLIENT_ID` | Secret | `production` environment | OIDC `azure/login` (federated identity client id) | `00000000-0000-0000-0000-000000000000` |
| `AZURE_TENANT_ID` | Secret | `production` environment | OIDC `azure/login` (Entra tenant id) | `00000000-0000-0000-0000-000000000000` |
| `AZURE_SUBSCRIPTION_ID` | Secret | `production` environment | OIDC `azure/login` (subscription id) | `00000000-0000-0000-0000-000000000000` |

> Adding, renaming, or removing any workflow variable/secret? Update this table in
> the same PR so it stays the single source of truth for CI configuration.

### Wiring up OIDC login (GitHub Actions → Azure)

OIDC lets the publish workflow obtain a short-lived Azure token from a **federated
credential** — no client secret is ever stored in GitHub. Set it up once. The
identity below is the same one E8 (#46) provisions as the ACA pull identity; if E8
has already created it, skip to step 4 and reuse it.

The commands use the Azure CLI (`az login` first). Replace the placeholder names.

1. **Create a user-assigned managed identity** (or an app registration) to act as
   the deploy principal:

   ```bash
   az identity create \
     --name classifier-gha-publisher \
     --resource-group <your-rg>
   # note the returned clientId, and your tenant/subscription ids:
   az account show --query '{subscriptionId:id, tenantId:tenantId}' -o json
   ```

2. **Grant it push rights** on the registry (scope to the ACR, least privilege):

   ```bash
   PRINCIPAL_ID=$(az identity show -n classifier-gha-publisher -g <your-rg> --query principalId -o tsv)
   ACR_ID=$(az acr show -n <your-acr-name> --query id -o tsv)
   az role assignment create --assignee "$PRINCIPAL_ID" --role AcrPush --scope "$ACR_ID"
   ```

3. **Add a federated credential** whose `subject` matches the token GitHub actually
   issues. The publish job declares `environment: production`, so GitHub sets the
   OIDC `subject` to the **environment** form (not the branch-ref form):
   `repo:<owner>/<repo>:environment:production`. Match it exactly:

   ```bash
   az identity federated-credential create \
     --name gha-production \
     --identity-name classifier-gha-publisher \
     --resource-group <your-rg> \
     --issuer https://token.actions.githubusercontent.com \
     --subject repo:<owner>/<repo>:environment:production \
     --audiences api://AzureADTokenExchange
   ```

   This subject covers both merge-to-`main` and manual `workflow_dispatch` runs,
   because both go through the `production` environment. (If you ever remove
   `environment:` from the job, the subject changes to `repo:<owner>/<repo>:ref:refs/heads/main`
   and this credential must be updated to match.)

4. **Store the variables and secrets** at the scopes in the reference table above:
   `ACR_LOGIN_SERVER` and `ACR_NAME` as **repository Variables**; `AZURE_CLIENT_ID`
   (the identity's clientId), `AZURE_TENANT_ID`, and `AZURE_SUBSCRIPTION_ID` as
   **secrets on the `production` environment** (*Settings → Environments → production
   → Environment secrets*; create the environment if it doesn't exist). Setting the
   `ACR_LOGIN_SERVER` repository variable is what flips the publish job from skipped
   to active.

The workflow already requests the token (`permissions: id-token: write`) and logs in
via `azure/login` with these three ids — no other changes are needed once the
credential and repo config exist.

## Test

```bash
uv run pytest
```

## Lint

```bash
uv run ruff check .
```

## Format

```bash
uv run ruff format .
```
