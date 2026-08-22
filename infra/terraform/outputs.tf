# Outputs consumed by the deploy runbook (README): the values fed into GitHub
# Actions config to un-gate publishing, the vault to seed manual secrets into (PR2),
# and the Postgres FQDN for connectivity checks. Sensitive values are marked so they
# never render in plan/apply logs.

output "resource_group_name" {
  description = "Resource group holding the pipeline."
  value       = azurerm_resource_group.main.name
}

output "acr_login_server" {
  description = "ACR login server — set as the ACR_LOGIN_SERVER GitHub repository variable to activate docker-publish.yml."
  value       = module.registry.login_server
}

output "acr_name" {
  description = "ACR name — set as the ACR_NAME GitHub repository variable (used by `az acr login`)."
  value       = module.registry.name
}

output "publisher_client_id" {
  description = "Client id of the GitHub Actions publisher identity — set as the AZURE_CLIENT_ID `production` environment secret."
  value       = module.identity.publisher_client_id
}

output "tenant_id" {
  description = "Entra tenant id — set as the AZURE_TENANT_ID `production` environment secret."
  value       = data.azurerm_client_config.current.tenant_id
}

output "subscription_id" {
  description = "Subscription id — set as the AZURE_SUBSCRIPTION_ID `production` environment secret."
  value       = var.subscription_id
}

output "runtime_identity_id" {
  description = "Resource id of the runtime managed identity (attached to the ACA jobs in PR2)."
  value       = module.identity.runtime_identity_id
}

output "runtime_identity_client_id" {
  description = "Client id of the runtime managed identity."
  value       = module.identity.runtime_client_id
}

output "key_vault_name" {
  description = "Key Vault to seed the manual secrets into (anthropic-api-key, graph-client-secret) in PR2."
  value       = module.keyvault.name
}

output "key_vault_uri" {
  description = "Key Vault URI (job secret references are built from it in PR2)."
  value       = module.keyvault.vault_uri
}

output "storage_account_name" {
  description = "Storage account name (queue host; KEDA scaler accountName in PR2)."
  value       = module.storage_queue.name
}

output "queue_endpoint" {
  description = "Queue service endpoint (CLASSIFIER__QUEUE_ACCOUNT_URL for the jobs in PR2)."
  value       = module.storage_queue.queue_endpoint
}

output "postgres_fqdn" {
  description = "PostgreSQL Flexible Server fully-qualified domain name."
  value       = module.database.fqdn
}

output "postgres_admin_username" {
  description = "PostgreSQL administrator login."
  value       = var.postgres_admin_username
}

output "database_connection_url" {
  description = "Assembled SQLAlchemy connection URL (also stored as the database-url Key Vault secret)."
  value       = module.database.connection_url
  sensitive   = true
}
