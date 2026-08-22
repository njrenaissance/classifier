# One-time bootstrap of the remote-state backend, run with LOCAL state (this is
# the chicken-and-egg step: it creates the storage the root config's azurerm
# backend then uses). Run once per subscription, before the first root `init`:
#
#   terraform -chdir=infra/terraform/bootstrap init
#   terraform -chdir=infra/terraform/bootstrap apply
#
# Then copy the outputs into environments/<env>.backend.hcl for the root init.
#
# Deliberately minimal and self-contained: no modules, no remote backend, no
# random suffix dependency on the root. Kept separate so a `terraform destroy` of
# the workload never touches the state account.

terraform {
  required_version = ">= 1.9.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
  }
}

provider "azurerm" {
  subscription_id = var.subscription_id
  features {}
}

resource "azurerm_resource_group" "tfstate" {
  name     = var.resource_group_name
  location = var.location
  tags = {
    project    = "classifier"
    purpose    = "terraform-state"
    managed_by = "terraform"
  }
}

resource "azurerm_storage_account" "tfstate" {
  name                     = var.storage_account_name
  resource_group_name      = azurerm_resource_group.tfstate.name
  location                 = azurerm_resource_group.tfstate.location
  account_tier             = "Standard"
  account_replication_type = "LRS"
  min_tls_version          = "TLS1_2"

  # State holds sensitive values; block public blob access and enforce versioning
  # so an accidental overwrite is recoverable.
  allow_nested_items_to_be_public = false

  blob_properties {
    versioning_enabled = true
  }

  tags = azurerm_resource_group.tfstate.tags
}

resource "azurerm_storage_container" "tfstate" {
  name                  = var.container_name
  storage_account_id    = azurerm_storage_account.tfstate.id
  container_access_type = "private"
}
