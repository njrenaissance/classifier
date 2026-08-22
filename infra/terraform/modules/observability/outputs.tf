output "workspace_id" {
  description = "Resource id of the Log Analytics workspace (consumed by the ACA environment in PR2)."
  value       = azurerm_log_analytics_workspace.main.id
}
