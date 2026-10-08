# dev environment inputs. Non-secret only (subscription/region/naming); no
# credentials live here — the DB password is generated, and the Anthropic/Graph
# secrets are seeded into Key Vault out of band (PR2). Safe to commit.

subscription_id = "2458052a-3cc8-43e3-a53b-e10df34a44d6"
location        = "eastus2"
environment     = "dev"

# name_prefix defaults to "classifier"; queue_name and Postgres sizing use the
# module defaults (Burstable B_Standard_B1ms, 32 GB). Override here if needed.

# ---------------------------------------------------------------------------
# Container Apps jobs (PR2). These three are REQUIRED and have no default — fill
# them in before `terraform apply`. They are ids, not secrets (safe to commit):
#   - graph_tenant_id / graph_client_id: the Entra app registration (ADR-0007),
#     Graph app-only perms Sites.Read.All + Files.Read.All, admin-consented.
#   - walker_drive_id: the target SharePoint document-library drive id.
# The Graph client secret and the Anthropic API key are NOT set here — they are
# seeded into Key Vault out of band (see infra/terraform/README.md, step 4).
# walker_root_path / cron / classification knobs fall back to the config.py-aligned
# defaults; override here only if a run needs different values.
graph_tenant_id = "REPLACE_WITH_ENTRA_TENANT_ID"
graph_client_id = "REPLACE_WITH_GRAPH_APP_CLIENT_ID"
walker_drive_id = "REPLACE_WITH_TARGET_DRIVE_ID"
