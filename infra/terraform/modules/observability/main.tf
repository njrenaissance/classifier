# Log Analytics workspace — the log sink for the ACA environment (ADR-0012, wired
# up in PR2's container_apps module).

resource "azurerm_log_analytics_workspace" "main" {
  name                = var.name
  resource_group_name = var.resource_group_name
  location            = var.location
  sku                 = "PerGB2018"
  retention_in_days   = 30
  tags                = var.tags
}
