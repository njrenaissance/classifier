# Storage account + the two queues (ADR-0012). The runtime managed identity gets
# Storage Queue Data Contributor (send/receive/delete) in modules/identity; the app
# authenticates to the queue with MI (CLASSIFIER__QUEUE_ACCOUNT_URL +
# CLASSIFIER__QUEUE_USE_MANAGED_IDENTITY=true). Neither Azurite nor the app
# auto-create the queues, so Terraform creates them here.

resource "azurerm_storage_account" "main" {
  name                     = var.name
  resource_group_name      = var.resource_group_name
  location                 = var.location
  account_tier             = "Standard"
  account_replication_type = "LRS"
  min_tls_version          = "TLS1_2"

  # Queue data plane is reached via managed identity; account-key access stays on
  # only because the KEDA scale rule (PR2) cannot authenticate with MI.
  shared_access_key_enabled = true

  tags = var.tags
}

resource "azurerm_storage_queue" "work" {
  name               = var.queue_name
  storage_account_id = azurerm_storage_account.main.id
}

# Poison / dead-letter queue. Provisioned now; the processor's move-to-poison logic
# (after N dequeues) is a noted application follow-up — Azure Queue Storage has no
# server-side max-dequeue-to-poison, and processor.py currently re-raises and lets
# dequeueCount climb.
resource "azurerm_storage_queue" "poison" {
  name               = "${var.queue_name}-poison"
  storage_account_id = azurerm_storage_account.main.id
}
