# Terraform — Azure infrastructure (E8, #46)

Provisions the two-job cloud pipeline's Azure resources (ADR-0012/0013) as IaC.
This is the **cloud** deployment; the sibling [`../docker-compose.yml`](../README.md)
stack is the local live-fire equivalent.

> **Landing in phases.** **PR1 (this)** provisions the foundation — resource group,
> Log Analytics, ACR, Storage + queues, PostgreSQL, Key Vault, and the two managed
> identities (runtime + GitHub-Actions publisher) with their RBAC. **PR2** adds the
> three Azure Container Apps jobs (walker / processor / migrate) and the end-to-end
> runbook. Only the `dev` environment is scaffolded so far.

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
│   └── identity/        # runtime + publisher managed identities, RBAC, federated credential
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

### 4. (PR2) Jobs, migrations, and end-to-end

Seeding the manual Key Vault secrets (`anthropic-api-key`, `graph-client-secret`),
deploying the ACA jobs, running the `migrate` job (`alembic upgrade head`), and
seeding `sync_state` for the first library are covered when `modules/container_apps`
lands in PR2.

## Secrets

- **Terraform-managed** (in encrypted remote state, marked `sensitive`):
  `database-url` — assembled from the generated Postgres password + FQDN + database.
- **Seeded out of band** (PR2, never in state): `anthropic-api-key`,
  `graph-client-secret` via `az keyvault secret set`.
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
| Container Registry | Standard | ~$20/mo |
| Container Apps jobs (PR2) | Consumption | ~$0 idle (processor scales to zero) |
| Storage Queue | Standard LRS | pennies (pay-per-operation) |
| Log Analytics | PerGB2018 | pay-per-GB ingested (low at this volume) |

Stop the Postgres server between test runs (`az postgres flexible-server stop`) to
keep dev cost near storage-only.

## Networking posture (first cut)

Public-endpoint Postgres with an `AllowAzureServices` firewall rule; ACR admin creds
disabled; Key Vault in RBAC-authorization mode. VNet / private endpoints and Entra
auth for Postgres are documented hardening follow-ups, out of scope for #46.
