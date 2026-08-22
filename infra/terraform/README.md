# Terraform — Azure infrastructure (E8, #46)

Provisions the two-job cloud pipeline's Azure resources (ADR-0012/0013) as IaC.
This is the **cloud** deployment; the sibling [`../docker-compose.yml`](../README.md)
stack is the local live-fire equivalent.

> **Landing in phases.** **PR1** provisioned the foundation — resource group,
> Log Analytics, ACR, Storage + queues, PostgreSQL, Key Vault, and the two managed
> identities (runtime + GitHub-Actions publisher) with their RBAC. **PR2 (this)**
> adds the Azure Container Apps managed environment and the three jobs (walker /
> processor / migrate), plus the end-to-end runbook (step 4). Only the `dev`
> environment is scaffolded so far.

## Layout

```
infra/terraform/
├── bootstrap/           # one-time: state resource group + storage account + tfstate container
├── *.tf                 # root: providers, backend, variables, locals, module wiring, outputs
├── modules/
│   ├── observability/   # Log Analytics workspace
│   ├── registry/        # Azure Container Registry (admin disabled)
│   ├── storage_queue/   # storage account + work queue + poison queue
│   ├── database/        # PostgreSQL Flexible Server (Burstable) + database + firewall
│   ├── keyvault/        # Key Vault (RBAC mode) + Terraform-managed database-url secret
│   ├── identity/        # runtime + publisher managed identities, RBAC, federated credential
│   └── container_apps/  # ACA managed environment + walker / processor / migrate jobs
└── environments/        # dev.tfvars + dev.backend.hcl  (prod added later)
```

## Prerequisites

- **Terraform** ≥ 1.9 and **Azure CLI**, logged in: `az login`.
- The `apply` principal needs, on the subscription: **Owner** or **User Access
  Administrator** (to create the role assignments in `modules/identity`) and **Key
  Vault Secrets Officer** on the vault (to write the `database-url` secret). These
  are assumed present, not granted by Terraform.
- Resource providers registered (idempotent):
  ```bash
  for ns in Microsoft.App Microsoft.ContainerRegistry Microsoft.DBforPostgreSQL \
            Microsoft.KeyVault Microsoft.OperationalInsights Microsoft.Storage \
            Microsoft.ManagedIdentity; do az provider register --namespace "$ns"; done
  ```

## Deploy runbook

### 0. Bootstrap the remote-state backend (once per subscription)

Creates the state resource group + storage account + container with **local**
state. Pick a globally-unique storage account name and keep it in sync with
`environments/dev.backend.hcl`.

```bash
terraform -chdir=infra/terraform/bootstrap init
terraform -chdir=infra/terraform/bootstrap apply \
  -var subscription_id=<sub-id> \
  -var storage_account_name=stclsfrtfstatedev
```

### 1. Init the root against the remote backend

```bash
terraform -chdir=infra/terraform init -backend-config=environments/dev.backend.hcl
```

### 2. Plan and apply

```bash
terraform -chdir=infra/terraform plan  -var-file=environments/dev.tfvars
terraform -chdir=infra/terraform apply -var-file=environments/dev.tfvars
```

The `container_apps` module needs `graph_tenant_id`, `graph_client_id`, and
`walker_drive_id` — `dev.tfvars` ships them as `REPLACE_WITH_*` placeholders so the
foundation applies cleanly on a first pass. Fill them in (step 4a) and re-apply
before the jobs can run for real; the placeholders let you stand up everything
else first.

### 3. Wire up GitHub Actions (un-gates image publishing)

`docker-publish.yml` is skipped until `ACR_LOGIN_SERVER` is set. From the outputs:

```bash
gh variable set ACR_LOGIN_SERVER --body "$(terraform -chdir=infra/terraform output -raw acr_login_server)"
gh variable set ACR_NAME         --body "$(terraform -chdir=infra/terraform output -raw acr_name)"
# create the `production` environment first if it doesn't exist, then:
gh secret set AZURE_CLIENT_ID       --env production --body "$(terraform -chdir=infra/terraform output -raw publisher_client_id)"
gh secret set AZURE_TENANT_ID       --env production --body "$(terraform -chdir=infra/terraform output -raw tenant_id)"
gh secret set AZURE_SUBSCRIPTION_ID --env production --body "$(terraform -chdir=infra/terraform output -raw subscription_id)"
```

`ACR_LOGIN_SERVER`/`ACR_NAME` are **repository Variables**; the three `AZURE_*` are
**`production` environment Secrets** (the federated credential trusts
`repo:<owner>/<repo>:environment:production`). See the root `README.md` GitHub
Actions configuration reference. Once set, the next merge to `main` publishes
`classifier:latest`.

### 4. Jobs, migrations, and end-to-end

The ACA environment and the three jobs (`job-walker`, `job-processor`,
`job-migrate`) are provisioned by the `apply` in step 2 — but the jobs won't run
successfully until (a) the two manual secrets are in Key Vault, (b) the Graph /
drive inputs are set in `dev.tfvars`, and (c) an image exists in ACR
(`docker-publish.yml`, activated in step 3, publishes `classifier:latest` on the
next merge to `main`).

**Inputs to gather first** (see the project root README for the Entra app setup):

- An **Entra app registration** (ADR-0007) with **application** Graph permissions
  `Sites.Read.All` + `Files.Read.All`, **admin-consented** → gives you the
  `tenant_id`, `client_id`, and a `client_secret`.
- The **`drive_id`** of the target SharePoint document library to classify.
- An **Anthropic API key**.

#### 4a. Fill the non-secret job inputs

`graph_tenant_id`, `graph_client_id`, and `walker_drive_id` are required (no
default) and are **ids, not secrets** — set them in `environments/dev.tfvars`
(replace the `REPLACE_WITH_*` placeholders). `walker_root_path`, `walker_cron`,
and the classification knobs (`self_consistency_n`, `temperature`,
`confidence_threshold`) fall back to the `src/config.py`-aligned defaults; override
in `dev.tfvars` only if a run needs different values.

#### 4b. Seed the manual Key Vault secrets

The Graph client secret and the Anthropic key are **never** in Terraform state —
seed them straight into the vault. The jobs reference them by **versionless** URI,
so this can run before *or* after `apply` and no redeploy is needed on rotation.
The `apply` principal already holds **Key Vault Secrets Officer** (a prerequisite).

```bash
VAULT=$(terraform -chdir=infra/terraform output -raw key_vault_name)
az keyvault secret set --vault-name "$VAULT" --name graph-client-secret --value '<graph-app-client-secret>'
az keyvault secret set --vault-name "$VAULT" --name anthropic-api-key   --value '<anthropic-api-key>'
```

#### 4c. Apply (creates/updates the jobs) and run the migration

Re-run step 2's `apply` after 4a so the jobs pick up the Graph/drive inputs, then
run the schema migration once as a manual job (`alembic upgrade head` — the
migrations are fix-forward only):

```bash
RG=$(terraform -chdir=infra/terraform output -raw resource_group_name)
MIGRATE=$(terraform -chdir=infra/terraform output -raw migrate_job_name)
az containerapp job start -g "$RG" -n "$MIGRATE"
az containerapp job execution list -g "$RG" -n "$MIGRATE" -o table   # wait for Succeeded
```

> The walker **auto-creates** its `sync_state` row from `CLASSIFIER__WALKER_DRIVE_ID`
> on its first run (`src/walker.py`) — there is **no manual `sync_state` seeding
> step**. `sync_state` is keyed on `drive_id` alone.

#### 4d. Verify end-to-end

Trigger a walk on demand (or wait for `walker_cron`), then watch a document flow
walker → queue → processor → a row in `documents`:

```bash
WALKER=$(terraform -chdir=infra/terraform output -raw walker_job_name)
az containerapp job start -g "$RG" -n "$WALKER"
# The walker enqueues candidates; the processor's KEDA azure-queue rule scales it
# 0 → 1 as the queue fills, downloads + classifies each item, and UPSERTs a row.
```

Confirm success by connecting to Postgres (`psql` via the `postgres_fqdn` output
and the generated admin password) and checking for rows in `documents`, and/or by
inspecting each job's execution logs in Log Analytics.

## Secrets

- **Terraform-managed** (in encrypted remote state, marked `sensitive`):
  `database-url` — assembled from the generated Postgres password + FQDN + database.
- **Seeded out of band** (step 4b, never in state): `anthropic-api-key`,
  `graph-client-secret` via `az keyvault secret set`.
- **Scaler-only** (a job secret, not in Key Vault): the processor holds the storage
  `queue-connection-string` because the KEDA `azure-queue` scale rule cannot
  authenticate with managed identity — its `authentication` block exposes only
  `secret_name`/`trigger_parameter`. The app runtime still works the queue with the
  runtime managed identity; only the scaler reads depth via the connection string.
- The ACA jobs reference secrets by **versionless Key Vault URI** + the runtime
  managed identity, so `plan` never depends on a seeded secret existing yet.

## Cost note (Burstable, not serverless — ADR-0013)

Azure Database for PostgreSQL Flexible Server has **no** Azure-SQL-style auto-pause.
The low-cost posture is the **Burstable** tier (`B_Standard_B1ms`) that can be
**stopped/started** (up to 7 days) when idle — not per-second pause. The rest is
mostly consumption-priced:

| Resource | Tier | Rough idle-most-of-day cost |
|---|---|---|
| PostgreSQL Flexible | `B_Standard_B1ms`, 32 GB | ~$13-16/mo running; ~storage-only when stopped |
| Container Registry | Basic (dev default) | ~$5/mo (10 GB); Standard ~$20/mo |
| Container Apps jobs | Consumption | ~$0 idle (processor scales to zero; walker runs on a cron) |
| Storage Queue | Standard LRS | pennies (pay-per-operation) |
| Log Analytics | PerGB2018 | pay-per-GB ingested (low at this volume) |

Stop the Postgres server between test runs (`az postgres flexible-server stop`) to
keep dev cost near storage-only.

## Networking posture (first cut)

Public-endpoint Postgres with an `AllowAzureServices` firewall rule; ACR admin creds
disabled; Key Vault in RBAC-authorization mode. VNet / private endpoints and Entra
auth for Postgres are documented hardening follow-ups, out of scope for #46.
