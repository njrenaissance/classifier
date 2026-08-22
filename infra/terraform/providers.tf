# Provider configuration. `subscription_id` is supplied per environment (tfvar or
# ARM_SUBSCRIPTION_ID); AzureRM 4.x requires it to be set explicitly rather than
# inferred. The principal running `apply` authenticates via `az login` / OIDC and
# needs Owner or User Access Administrator to create the role assignments in
# modules/identity, plus Key Vault Secrets Officer to write the database-url secret.

provider "azurerm" {
  subscription_id = var.subscription_id

  features {
    key_vault {
      # Recover soft-deleted vaults on re-apply instead of colliding on the name.
      recover_soft_deleted_key_vaults = true
      purge_soft_delete_on_destroy    = false
    }
  }
}

provider "random" {}
