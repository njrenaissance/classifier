output "runtime_identity_id" {
  description = "Resource id of the runtime managed identity (attach to the ACA jobs)."
  value       = azurerm_user_assigned_identity.runtime.id
}

output "runtime_client_id" {
  description = "Client id of the runtime managed identity (DefaultAzureCredential picks it up)."
  value       = azurerm_user_assigned_identity.runtime.client_id
}

output "runtime_principal_id" {
  description = "Principal (object) id of the runtime managed identity."
  value       = azurerm_user_assigned_identity.runtime.principal_id
}

output "publisher_identity_id" {
  description = "Resource id of the publisher managed identity."
  value       = azurerm_user_assigned_identity.publisher.id
}

output "publisher_client_id" {
  description = "Client id of the publisher identity — the AZURE_CLIENT_ID GitHub `production` environment secret."
  value       = azurerm_user_assigned_identity.publisher.client_id
}
