output "id" {
  description = "Resource id of the storage account (RBAC scope for Storage Queue Data Contributor)."
  value       = azurerm_storage_account.main.id
}

output "name" {
  description = "Storage account name (KEDA scaler metadata accountName in PR2)."
  value       = azurerm_storage_account.main.name
}

output "queue_endpoint" {
  description = "Queue service endpoint (CLASSIFIER__QUEUE_ACCOUNT_URL for managed-identity access)."
  value       = azurerm_storage_account.main.primary_queue_endpoint
}

output "primary_connection_string" {
  description = "Account connection string — used ONLY by the KEDA scale rule (PR2), which cannot authenticate with managed identity."
  value       = azurerm_storage_account.main.primary_connection_string
  sensitive   = true
}

output "work_queue_name" {
  description = "Work queue name."
  value       = azurerm_storage_queue.work.name
}

output "poison_queue_name" {
  description = "Poison / dead-letter queue name."
  value       = azurerm_storage_queue.poison.name
}
