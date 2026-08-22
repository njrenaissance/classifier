# Azure Container Registry. Admin creds disabled (E8 requirement); image pull is via
# the runtime managed identity's AcrPull, push via the publisher identity's AcrPush —
# both role assignments live in modules/identity.

resource "azurerm_container_registry" "main" {
  name                = var.name
  resource_group_name = var.resource_group_name
  location            = var.location
  sku                 = "Standard"
  admin_enabled       = false
  tags                = var.tags
}
