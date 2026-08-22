# Outputs map directly onto the root backend config keys. After apply, write these
# into environments/<env>.backend.hcl (resource_group_name, storage_account_name,
# container_name) — the state `key` is chosen per environment there.

output "resource_group_name" {
  description = "State resource group (backend `resource_group_name`)."
  value       = azurerm_resource_group.tfstate.name
}

output "storage_account_name" {
  description = "State storage account (backend `storage_account_name`)."
  value       = azurerm_storage_account.tfstate.name
}

output "container_name" {
  description = "State container (backend `container_name`)."
  value       = azurerm_storage_container.tfstate.name
}
