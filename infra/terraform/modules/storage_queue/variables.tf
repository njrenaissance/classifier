variable "name" {
  description = "Storage account name (globally unique, 3-24 lowercase alphanumeric)."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group to create the account in."
  type        = string
}

variable "location" {
  description = "Azure region."
  type        = string
}

variable "queue_name" {
  description = "Work-queue name. The poison queue is <queue_name>-poison."
  type        = string
}

variable "tags" {
  description = "Resource tags."
  type        = map(string)
  default     = {}
}
