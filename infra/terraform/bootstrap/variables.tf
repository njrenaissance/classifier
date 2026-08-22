variable "subscription_id" {
  description = "Target Azure subscription id (also settable via ARM_SUBSCRIPTION_ID)."
  type        = string
}

variable "location" {
  description = "Azure region for the state resource group / storage account."
  type        = string
  default     = "eastus2"
}

variable "resource_group_name" {
  description = "Resource group holding the Terraform state storage account."
  type        = string
  default     = "rg-classifier-tfstate"
}

variable "storage_account_name" {
  description = "Globally-unique storage account name for Terraform state (3-24 lowercase alphanumeric)."
  type        = string
}

variable "container_name" {
  description = "Blob container holding the state files."
  type        = string
  default     = "tfstate"
}
