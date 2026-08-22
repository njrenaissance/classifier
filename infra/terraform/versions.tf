# Provider and Terraform version constraints for the classifier pipeline (E8, #46).
# AzureRM 4.x is the target (it also provisions the GitHub Actions publisher's
# federated identity credential via azurerm_federated_identity_credential); random
# generates the globally-unique resource suffix plus the Postgres admin password.

terraform {
  required_version = ">= 1.9.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.0"
    }
  }
}
