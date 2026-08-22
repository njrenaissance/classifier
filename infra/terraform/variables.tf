# Root input variables. Per-environment values live in environments/<env>.tfvars.
# PR1 (foundation) variables come first; the Graph / walker / classification-knob
# variables the ACA jobs consume (PR2, modules/container_apps) follow at the end.

# ---------------------------------------------------------------------------
# Subscription / placement
# ---------------------------------------------------------------------------

variable "subscription_id" {
  description = "Target Azure subscription id (also settable via ARM_SUBSCRIPTION_ID)."
  type        = string
}

variable "location" {
  description = "Azure region for all resources (e.g. \"eastus2\")."
  type        = string
  default     = "eastus2"
}

variable "name_prefix" {
  description = "Short lowercase token woven into every resource name. Keep it brief: it feeds globally-unique names (ACR, storage, Key Vault) that cap at 24 characters."
  type        = string
  default     = "classifier"

  validation {
    condition     = can(regex("^[a-z][a-z0-9]{2,11}$", var.name_prefix))
    error_message = "name_prefix must be 3-12 chars, lowercase alphanumeric, starting with a letter."
  }
}

variable "environment" {
  description = "Deployment environment discriminator (\"dev\" or \"prod\"). Names and tags derive from it."
  type        = string

  validation {
    condition     = contains(["dev", "prod"], var.environment)
    error_message = "environment must be \"dev\" or \"prod\"."
  }
}

variable "tags" {
  description = "Extra tags merged onto the standard project/environment/managed_by tag set."
  type        = map(string)
  default     = {}
}

# ---------------------------------------------------------------------------
# PostgreSQL (ADR-0013) — Burstable, no serverless auto-pause
# ---------------------------------------------------------------------------

variable "postgres_sku_name" {
  description = "Flexible Server SKU. Burstable tier per ADR-0013 (e.g. B_Standard_B1ms, B_Standard_B2s)."
  type        = string
  default     = "B_Standard_B1ms"
}

variable "postgres_storage_mb" {
  description = "Flexible Server storage in MB (minimum 32768)."
  type        = number
  default     = 32768
}

variable "postgres_version" {
  description = "PostgreSQL major version."
  type        = string
  default     = "16"
}

variable "postgres_admin_username" {
  description = "Administrator login for the Flexible Server. The password is generated (random_password) and never committed as plaintext."
  type        = string
  default     = "classifier_admin"
}

variable "postgres_database_name" {
  description = "Application database created on the server."
  type        = string
  default     = "classifier"
}

# ---------------------------------------------------------------------------
# Container Registry
# ---------------------------------------------------------------------------

variable "acr_sku" {
  description = "ACR SKU. Basic (default) is the cheap dev choice (~$5/mo, 10 GB); Standard/Premium for higher storage/throughput."
  type        = string
  default     = "Basic"
}

# ---------------------------------------------------------------------------
# Queue
# ---------------------------------------------------------------------------

variable "queue_name" {
  description = "Work-queue name shared by walker (producer) and processor (consumer). The poison queue is <queue_name>-poison."
  type        = string
  default     = "classifier-work-items"
}

# ---------------------------------------------------------------------------
# GitHub Actions publisher identity (decision 4)
# ---------------------------------------------------------------------------

variable "github_repository" {
  description = "owner/repo the publisher federated credential trusts (subject repo:<owner>/<repo>:environment:<env>)."
  type        = string
  default     = "njrenaissance/classifier"
}

variable "github_environment" {
  description = "GitHub Actions environment gating the publish workflow; part of the federated-credential subject."
  type        = string
  default     = "production"
}

# ---------------------------------------------------------------------------
# Container Apps jobs (PR2) — image, Graph credentials, walker + classification
# knobs. Secrets (Graph client secret, Anthropic key) are NOT variables — they are
# seeded into Key Vault out of band and referenced by the jobs (see the runbook).
# Non-secret defaults mirror DEFAULTS in src/config.py.
# ---------------------------------------------------------------------------

variable "image_tag" {
  description = "Tag of the classifier image the ACA jobs run (docker-publish.yml pushes :latest and :<sha>)."
  type        = string
  default     = "latest"
}

variable "graph_tenant_id" {
  description = "Entra tenant id of the Graph app registration (ADR-0007). Supplied at apply time; not a secret."
  type        = string
}

variable "graph_client_id" {
  description = "Client (application) id of the Graph app registration. Supplied at apply time; not a secret."
  type        = string
}

variable "walker_drive_id" {
  description = "SharePoint document-library drive id the walker enumerates and the processor downloads from."
  type        = string
}

variable "walker_root_path" {
  description = "Subtree the walk is scoped to; / or empty walks the whole drive (ADR-0019)."
  type        = string
  default     = "/Matters"
}

variable "walker_time_budget_seconds" {
  description = "Wall-clock budget for one scheduled walk; a large first enumeration resumes across runs."
  type        = number
  default     = 600
}

variable "walker_trigger_mode" {
  description = "How the walker runs: \"manual\" (on demand, the default for validation) or \"schedule\" (activates walker_cron)."
  type        = string
  default     = "manual"

  validation {
    condition     = contains(["manual", "schedule"], var.walker_trigger_mode)
    error_message = "walker_trigger_mode must be \"manual\" or \"schedule\"."
  }
}

variable "walker_cron" {
  description = "Cron expression (UTC) for the scheduled walker run; only used when walker_trigger_mode = \"schedule\". Default: every 6 hours."
  type        = string
  default     = "0 */6 * * *"
}

variable "processor_max_executions" {
  description = "KEDA max concurrent processor replicas (scales from 0 to this on queue depth)."
  type        = number
  default     = 10
}

variable "self_consistency_n" {
  description = "Self-consistency sample count per document (CLASSIFIER_N)."
  type        = number
  default     = 5
}

variable "temperature" {
  description = "Sampling temperature for classification (CLASSIFIER_TEMPERATURE)."
  type        = number
  default     = 0.4
}

variable "confidence_threshold" {
  description = "Minimum agreement fraction to accept a label (CLASSIFIER_CONFIDENCE_THRESHOLD)."
  type        = number
  default     = 0.6
}
