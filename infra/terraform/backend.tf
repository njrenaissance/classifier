# Remote state — azurerm backend in PARTIAL configuration. Nothing environment-
# specific lives here; the real backend values (resource group, storage account,
# container, state key) are supplied at init time per environment:
#
#   terraform init -backend-config=environments/dev.backend.hcl
#
# The backing storage account + tfstate container are created once by bootstrap/
# (step 0 of the deploy runbook) before the first init here.

terraform {
  backend "azurerm" {
    use_azuread_auth = true
  }
}
