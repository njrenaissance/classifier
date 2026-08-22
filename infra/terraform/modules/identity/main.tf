# Two user-assigned managed identities, least privilege:
#
#   Runtime MI   — attached to the three ACA jobs (PR2). Pulls the image, reads
#                  secrets, and works the queue. NO push rights.
#   Publisher MI — used only by GitHub Actions (OIDC) to push images. Trusts the
#                  repo's `production` environment via a federated credential; holds
#                  AcrPush and nothing else.
#
# Role assignments use principal_type = "ServicePrincipal" so Azure doesn't fail the
# assignment while the freshly-created identity's SP replicates through Entra.

resource "azurerm_user_assigned_identity" "runtime" {
  name                = var.runtime_identity_name
  resource_group_name = var.resource_group_name
  location            = var.location
  tags                = var.tags
}

resource "azurerm_user_assigned_identity" "publisher" {
  name                = var.publisher_identity_name
  resource_group_name = var.resource_group_name
  location            = var.location
  tags                = var.tags
}

# --- Runtime RBAC ---------------------------------------------------------

resource "azurerm_role_assignment" "runtime_acr_pull" {
  scope                = var.acr_id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.runtime.principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "runtime_kv_secrets_user" {
  scope                = var.key_vault_id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.runtime.principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "runtime_queue_contributor" {
  scope                = var.storage_account_id
  role_definition_name = "Storage Queue Data Contributor"
  principal_id         = azurerm_user_assigned_identity.runtime.principal_id
  principal_type       = "ServicePrincipal"
}

# --- Publisher RBAC + federated credential --------------------------------

resource "azurerm_role_assignment" "publisher_acr_push" {
  scope                = var.acr_id
  role_definition_name = "AcrPush"
  principal_id         = azurerm_user_assigned_identity.publisher.principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_federated_identity_credential" "publisher_github" {
  name                = "github-actions-${var.github_environment}"
  resource_group_name = var.resource_group_name
  parent_id           = azurerm_user_assigned_identity.publisher.id
  audience            = ["api://AzureADTokenExchange"]
  issuer              = "https://token.actions.githubusercontent.com"
  subject             = "repo:${var.github_repository}:environment:${var.github_environment}"
}
