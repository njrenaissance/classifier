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

  # Terraform-managed secret: the assembled DB connection URL (a generated infra
  # credential). The Anthropic key and Graph client secret are seeded out of band
  # in PR2 and never enter Terraform state.
  secret_name_database_url = "database-url"

  tags = merge(
    {
      project     = "classifier"
      environment = var.environment
      managed_by  = "terraform"
    },
    var.tags,
  )
}
