# Root module wiring (PR1 — foundation). One resource group holds everything; each
# concern is a module. Dependency order is expressed through input references
# (not depends_on): identity grants RBAC on the registry, key vault, and storage it
# receives ids for; keyvault stores the URL the database module assembles. The ACA
# jobs (modules/container_apps) consume these outputs.

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

module "container_apps" {
  source = "./modules/container_apps"

  resource_group_name        = azurerm_resource_group.main.name
  location                   = azurerm_resource_group.main.location
  environment_name           = local.container_app_environment_name
  log_analytics_workspace_id = module.observability.workspace_id

  # Image + registry + runtime identity.
  image                      = local.image_ref
  registry_server            = module.registry.login_server
  runtime_identity_id        = module.identity.runtime_identity_id
  runtime_identity_client_id = module.identity.runtime_client_id

  # Key Vault secret references (versionless).
  database_url_secret_id      = local.database_url_secret_id
  graph_client_secret_id      = local.graph_client_secret_secret_id
  anthropic_api_key_secret_id = local.anthropic_api_key_secret_id

  # Queue: managed identity at run time; connection string for the KEDA scaler only.
  queue_account_url       = module.storage_queue.queue_endpoint
  queue_name              = module.storage_queue.work_queue_name
  queue_connection_string = module.storage_queue.primary_connection_string
  storage_account_name    = module.storage_queue.name

  # Graph app-only credentials (ids plain; secret is the Key Vault ref above).
  graph_tenant_id = var.graph_tenant_id
  graph_client_id = var.graph_client_id

  # Walker + processor knobs.
  walker_drive_id            = var.walker_drive_id
  walker_root_path           = var.walker_root_path
  walker_time_budget_seconds = var.walker_time_budget_seconds
  walker_trigger_mode        = var.walker_trigger_mode
  walker_cron                = var.walker_cron
  processor_max_executions   = var.processor_max_executions
  self_consistency_n         = var.self_consistency_n
  temperature                = var.temperature
  confidence_threshold       = var.confidence_threshold

  tags = local.tags
}
