# Key Vault in RBAC-authorization mode (no access policies). The runtime managed
# identity gets Key Vault Secrets User in modules/identity; the principal running
# `terraform apply` must already hold Key Vault Secrets Officer to create the
# database-url secret below (a deploy prerequisite — see the README).
#
# Only the database URL is Terraform-managed (a generated infra credential). The
# Anthropic key and Graph client secret are seeded out of band via
# `az keyvault secret set` (PR2), so they never enter Terraform state.

resource "azurerm_key_vault" "main" {
  name                = var.name
  resource_group_name = var.resource_group_name
  location            = var.location
  tenant_id           = var.tenant_id

  sku_name                   = "standard"
  rbac_authorization_enabled = true
  purge_protection_enabled   = false
  soft_delete_retention_days = 7

  tags = var.tags
}

resource "azurerm_key_vault_secret" "database_url" {
  name         = var.database_url_secret_name
  value        = var.database_url_secret_value
  key_vault_id = azurerm_key_vault.main.id
}
