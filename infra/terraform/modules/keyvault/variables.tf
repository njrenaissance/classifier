variable "name" {
  description = "Key Vault name (globally unique, 3-24 chars)."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group to create the vault in."
  type        = string
}

variable "location" {
  description = "Azure region."
  type        = string
}

variable "tenant_id" {
  description = "Entra tenant id for the vault."
  type        = string
}

variable "database_url_secret_name" {
  description = "Name of the Terraform-managed database URL secret."
  type        = string
}

variable "database_url_secret_value" {
  description = "Assembled database connection URL to store."
  type        = string
  sensitive   = true
}

variable "tags" {
  description = "Resource tags."
  type        = map(string)
  default     = {}
}
