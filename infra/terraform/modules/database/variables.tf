variable "name" {
  description = "Flexible Server name."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group to create the server in."
  type        = string
}

variable "location" {
  description = "Azure region."
  type        = string
}

variable "sku_name" {
  description = "Flexible Server SKU (Burstable tier)."
  type        = string
}

variable "storage_mb" {
  description = "Storage in MB."
  type        = number
}

variable "postgres_version" {
  description = "PostgreSQL major version."
  type        = string
}

variable "admin_username" {
  description = "Administrator login."
  type        = string
}

variable "database_name" {
  description = "Application database name."
  type        = string
}

variable "tags" {
  description = "Resource tags."
  type        = map(string)
  default     = {}
}
