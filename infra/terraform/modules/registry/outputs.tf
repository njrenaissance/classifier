output "id" {
  description = "Resource id of the registry (RBAC scope for AcrPull/AcrPush)."
  value       = azurerm_container_registry.main.id
}

output "login_server" {
  description = "Registry login server (e.g. crclassifierdev.azurecr.io)."
  value       = azurerm_container_registry.main.login_server
}

output "name" {
  description = "Registry name."
  value       = azurerm_container_registry.main.name
}
