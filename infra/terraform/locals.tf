# Naming convention, common tags, and the Terraform-managed secret name. Globally-
# unique resource names (ACR, storage, Key Vault) get a short random suffix and are
# stripped of hyphens / clamped to their length caps.

locals {
  suffix = random_string.suffix.result

  # Standard, human-readable names (hyphens allowed).
  resource_group_name = "rg-${var.name_prefix}-${var.environment}"
  postgres_name       = "psql-${var.name_prefix}-${var.environment}-${local.suffix}"
  log_analytics_name  = "log-${var.name_prefix}-${var.environment}"

  # Globally-unique names with charset/length constraints.
  #   ACR: 5-50 alphanumeric only.
  #   Storage account: 3-24 lowercase alphanumeric only.
  #   Key Vault: 3-24, alphanumeric + hyphens.
  acr_name       = substr(replace("cr${var.name_prefix}${var.environment}${local.suffix}", "-", ""), 0, 50)
  storage_name   = substr(replace("st${var.name_prefix}${var.environment}${local.suffix}", "-", ""), 0, 24)
  key_vault_name = substr("kv-${var.name_prefix}-${local.suffix}", 0, 24)

  # User-assigned identities.
  runtime_identity_name   = "id-${var.name_prefix}-runtime-${var.environment}"
  publisher_identity_name = "id-${var.name_prefix}-gha-publisher"

  # Container Apps managed environment (hyphens allowed).
  container_app_environment_name = "cae-${var.name_prefix}-${var.environment}"

  # Image the three ACA jobs run — the ACR login server + repo + tag.
  image_ref = "${module.registry.login_server}/classifier:${var.image_tag}"

  # Key Vault secret names. Only database-url is Terraform-managed (a generated
  # infra credential); the Anthropic key and Graph client secret are seeded out of
  # band via `az keyvault secret set` and never enter Terraform state. The jobs
  # reference all three by versionless Key Vault URI, so a plan never depends on a
  # seeded secret already existing.
  secret_name_database_url        = "database-url"
  secret_name_anthropic_api_key   = "anthropic-api-key"
  secret_name_graph_client_secret = "graph-client-secret"

  # Versionless Key Vault secret ids (URI without a version suffix), so secret
  # rotation needs no job redeploy.
  database_url_secret_id        = "${module.keyvault.vault_uri}secrets/${local.secret_name_database_url}"
  anthropic_api_key_secret_id   = "${module.keyvault.vault_uri}secrets/${local.secret_name_anthropic_api_key}"
  graph_client_secret_secret_id = "${module.keyvault.vault_uri}secrets/${local.secret_name_graph_client_secret}"

  tags = merge(
    {
      project     = "classifier"
      environment = var.environment
      managed_by  = "terraform"
    },
    var.tags,
  )
}
