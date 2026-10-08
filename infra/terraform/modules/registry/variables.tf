variable "name" {
  description = "Container registry name (globally unique, alphanumeric)."
  type        = string
}

variable "sku" {
  description = "Registry SKU. Basic (dev default) includes 10 GB and is plenty for one image; Standard/Premium raise storage + throughput."
  type        = string
  default     = "Basic"

  validation {
    condition     = contains(["Basic", "Standard", "Premium"], var.sku)
    error_message = "sku must be one of Basic, Standard, Premium."
  }
}

variable "resource_group_name" {
  description = "Resource group to create the registry in."
  type        = string
}

variable "location" {
  description = "Azure region."
  type        = string
}

variable "tags" {
  description = "Resource tags."
  type        = map(string)
  default     = {}
}
