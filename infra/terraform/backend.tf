# Remote state — azurerm backend in PARTIAL configuration. Nothing environment-
# specific lives here; the real backend values (resource group, storage account,
# container, state key) are supplied at init time per environment:
#
#   terraform init -backend-config=environments/dev.backend.hcl
#
# The backing storage account + tfstate container are created once by bootstrap/
# (step 0 of the deploy runbook) before the first init here.
#
# State auth: the azurerm backend resolves the storage account key via ARM
# `listKeys` using the caller's Azure credentials (control-plane access to the
# state account suffices). To switch to AAD data-plane auth instead, grant the
# caller "Storage Blob Data Contributor" on the state account and add
# `use_azuread_auth = true` here.

terraform {
  backend "azurerm" {}
}
