# Inputs for the Container Apps module: the ACA environment plus the three jobs
# (walker / processor / migrate). Every value is supplied by the root module from
# the PR1 foundation outputs (registry, identity, key vault, storage_queue,
# observability) — this module creates no dependencies of its own.

variable "resource_group_name" {
  description = "Resource group that holds the ACA environment and jobs."
  type        = string
}

variable "location" {
  description = "Azure region for the ACA environment and jobs."
  type        = string
}

variable "tags" {
  description = "Tags applied to every resource in this module."
  type        = map(string)
  default     = {}
}

variable "environment_name" {
  description = "Name of the Container Apps managed environment."
  type        = string
}

variable "log_analytics_workspace_id" {
  description = "Log Analytics workspace the ACA environment ships console/system logs to."
  type        = string
}

# ---------------------------------------------------------------------------
# Image + registry + identity
# ---------------------------------------------------------------------------

variable "image" {
  description = "Fully-qualified image ref the jobs run (e.g. crclassifierdev.azurecr.io/classifier:latest)."
  type        = string
}

variable "registry_server" {
  description = "ACR login server for the registry block (image is pulled with the runtime identity)."
  type        = string
}

variable "runtime_identity_id" {
  description = "Resource id of the runtime user-assigned identity attached to every job (ACR pull, Key Vault read, queue access)."
  type        = string
}

variable "runtime_identity_client_id" {
  description = "Client id of the runtime identity, exported as AZURE_CLIENT_ID so DefaultAzureCredential selects it for managed-identity queue access."
  type        = string
}

# ---------------------------------------------------------------------------
# Key Vault secret ids (versionless URIs, referenced with the runtime identity)
# ---------------------------------------------------------------------------

variable "database_url_secret_id" {
  description = "Versionless Key Vault secret id for the database connection URL (database-url)."
  type        = string
}

variable "graph_client_secret_id" {
  description = "Versionless Key Vault secret id for the Graph app client secret (graph-client-secret), seeded out of band."
  type        = string
}

variable "anthropic_api_key_secret_id" {
  description = "Versionless Key Vault secret id for the Anthropic API key (anthropic-api-key), seeded out of band."
  type        = string
}

# ---------------------------------------------------------------------------
# Queue (runtime uses managed identity; the KEDA scaler uses the connection string)
# ---------------------------------------------------------------------------

variable "queue_account_url" {
  description = "Queue service endpoint (CLASSIFIER__QUEUE_ACCOUNT_URL for managed-identity access)."
  type        = string
}

variable "queue_name" {
  description = "Work queue name shared by walker and processor."
  type        = string
}

variable "queue_connection_string" {
  description = "Storage account connection string — used ONLY by the processor's KEDA scale rule, which cannot authenticate with managed identity."
  type        = string
  sensitive   = true
}

variable "storage_account_name" {
  description = "Storage account name (KEDA scaler metadata accountName)."
  type        = string
}

# ---------------------------------------------------------------------------
# Graph app-only credentials (ids are plain; the secret is a Key Vault ref)
# ---------------------------------------------------------------------------

variable "graph_tenant_id" {
  description = "Entra tenant id of the Graph app registration (CLASSIFIER__GRAPH_TENANT_ID)."
  type        = string
}

variable "graph_client_id" {
  description = "Client (application) id of the Graph app registration (CLASSIFIER__GRAPH_CLIENT_ID)."
  type        = string
}

# ---------------------------------------------------------------------------
# Walker job knobs
# ---------------------------------------------------------------------------

variable "walker_drive_id" {
  description = "SharePoint document-library drive id the walker enumerates (CLASSIFIER__WALKER_DRIVE_ID)."
  type        = string
}

variable "walker_root_path" {
  description = "Subtree the walk is scoped to (CLASSIFIER__WALKER_ROOT_PATH); / or empty walks the whole drive."
  type        = string
  default     = "/Matters"
}

variable "walker_time_budget_seconds" {
  description = "Wall-clock budget for one scheduled walk (CLASSIFIER__WALKER_TIME_BUDGET_SECONDS); a large first enumeration resumes across runs."
  type        = number
  default     = 600
}

variable "walker_trigger_mode" {
  description = "How the walker is triggered: \"manual\" (run on demand via `az containerapp job start`) or \"schedule\" (walker_cron). Manual is the default for initial validation; flip to schedule to activate the cron."
  type        = string
  default     = "manual"

  validation {
    condition     = contains(["manual", "schedule"], var.walker_trigger_mode)
    error_message = "walker_trigger_mode must be \"manual\" or \"schedule\"."
  }
}

variable "walker_cron" {
  description = "Cron expression for the scheduled walker run (UTC). Only used when walker_trigger_mode = \"schedule\"."
  type        = string
  default     = "0 */6 * * *"
}

# ---------------------------------------------------------------------------
# Processor job knobs
# ---------------------------------------------------------------------------

variable "processor_max_executions" {
  description = "KEDA max concurrent processor replicas (scales from 0 to this on queue depth)."
  type        = number
  default     = 10
}

variable "self_consistency_n" {
  description = "Self-consistency sample count (CLASSIFIER_N)."
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

# ---------------------------------------------------------------------------
# Compute sizing (valid Consumption cpu/memory pairs — e.g. 0.5/1Gi, 1.0/2Gi)
# ---------------------------------------------------------------------------

variable "walker_cpu" {
  description = "vCPU allocated to the walker container."
  type        = number
  default     = 0.5
}

variable "walker_memory" {
  description = "Memory allocated to the walker container (must pair with walker_cpu)."
  type        = string
  default     = "1Gi"
}

variable "processor_cpu" {
  description = "vCPU allocated to the processor container (extraction + inference)."
  type        = number
  default     = 1.0
}

variable "processor_memory" {
  description = "Memory allocated to the processor container (must pair with processor_cpu)."
  type        = string
  default     = "2Gi"
}

variable "migrate_cpu" {
  description = "vCPU allocated to the migrate container."
  type        = number
  default     = 0.5
}

variable "migrate_memory" {
  description = "Memory allocated to the migrate container (must pair with migrate_cpu)."
  type        = string
  default     = "1Gi"
}

variable "job_replica_timeout_seconds" {
  description = "Per-replica timeout for the walker and processor jobs; comfortably above the walker time budget."
  type        = number
  default     = 1800
}

variable "migrate_replica_timeout_seconds" {
  description = "Per-replica timeout for the migrate job (alembic upgrade head is quick)."
  type        = number
  default     = 600
}
