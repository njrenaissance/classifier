variable "resource_group_name" {
  description = "Resource group to create the identities in."
  type        = string
}

variable "location" {
  description = "Azure region."
  type        = string
}

variable "runtime_identity_name" {
  description = "Name of the runtime managed identity (attached to the ACA jobs)."
  type        = string
}

variable "publisher_identity_name" {
  description = "Name of the GitHub Actions publisher managed identity."
  type        = string
}

variable "acr_id" {
  description = "Container registry resource id (AcrPull + AcrPush scope)."
  type        = string
}

variable "key_vault_id" {
  description = "Key Vault resource id (Key Vault Secrets User scope)."
  type        = string
}

variable "storage_account_id" {
  description = "Storage account resource id (Storage Queue Data Contributor scope)."
  type        = string
}

variable "github_repository" {
  description = "owner/repo trusted by the publisher federated credential."
  type        = string
}

variable "github_environment" {
  description = "GitHub Actions environment in the federated-credential subject."
  type        = string
}

variable "tags" {
  description = "Resource tags."
  type        = map(string)
  default     = {}
}
