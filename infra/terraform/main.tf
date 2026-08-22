# Root module wiring (PR1 — foundation). One resource group holds everything; each
# concern is a module. Dependency order is expressed through input references
# (not depends_on): identity grants RBAC on the registry, key vault, and storage it
# receives ids for; keyvault stores the URL the database module assembles. The ACA
# jobs (modules/container_apps) land in PR2 and consume these outputs.

data "azurerm_client_config" "current" {}

resource "random_string" "suffix" {
  length  = 6
  lower   = true
  upper   = false
  numeric = true
  special = false
}

resource "azurerm_resource_group" "main" {
  name     = local.resource_group_name
  location = var.location
  tags     = local.tags
}

module "observability" {
  source = "./modules/observability"

  name                = local.log_analytics_name
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  tags                = local.tags
}

module "registry" {
  source = "./modules/registry"

  name                = local.acr_name
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku                 = var.acr_sku
  tags                = local.tags
}

module "storage_queue" {
  source = "./modules/storage_queue"

  name                = local.storage_name
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  queue_name          = var.queue_name
  tags                = local.tags
}

module "database" {
  source = "./modules/database"

  name                = local.postgres_name
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku_name            = var.postgres_sku_name
  storage_mb          = var.postgres_storage_mb
  postgres_version    = var.postgres_version
  admin_username      = var.postgres_admin_username
  database_name       = var.postgres_database_name
  tags                = local.tags
}

module "keyvault" {
  source = "./modules/keyvault"

  name                = local.key_vault_name
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  tenant_id           = data.azurerm_client_config.current.tenant_id

  database_url_secret_name  = local.secret_name_database_url
  database_url_secret_value = module.database.connection_url

  tags = local.tags
}

module "identity" {
  source = "./modules/identity"

  resource_group_name     = azurerm_resource_group.main.name
  location                = azurerm_resource_group.main.location
  runtime_identity_name   = local.runtime_identity_name
  publisher_identity_name = local.publisher_identity_name

  # RBAC scopes (least privilege).
  acr_id             = module.registry.id
  key_vault_id       = module.keyvault.id
  storage_account_id = module.storage_queue.id

  # Publisher federated credential (GitHub Actions OIDC).
  github_repository  = var.github_repository
  github_environment = var.github_environment

  tags = local.tags
}
