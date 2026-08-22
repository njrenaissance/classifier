output "id" {
  description = "Resource id of the vault (RBAC scope for Key Vault Secrets User)."
  value       = azurerm_key_vault.main.id
}

output "name" {
  description = "Vault name (seed manual secrets into this vault in PR2)."
  value       = azurerm_key_vault.main.name
}

output "vault_uri" {
  description = "Vault URI (e.g. https://kv-classifier-xxxx.vault.azure.net/); job secret references are built from it in PR2."
  value       = azurerm_key_vault.main.vault_uri
}
