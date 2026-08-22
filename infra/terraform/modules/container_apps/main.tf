# Azure Container Apps: one managed environment and the three jobs of the cloud
# pipeline (ADR-0012). All three run the SAME image and differ only by command
# override and environment slice (see src/config.py):
#
#   walker    scheduled SharePoint delta walk (producer)   — python -m walker
#   processor queue-triggered classify + UPSERT (consumer) — python -m processor
#   migrate   schema migration, run on demand              — alembic upgrade head
#
# Every job runs as the runtime user-assigned identity: it pulls the image
# (AcrPull), reads Key Vault secrets by versionless URI (Secrets User), and works
# the queue (Queue Data Contributor) — all granted in modules/identity. Secrets
# reach the jobs as Key Vault references resolved at run time, so a `plan` never
# depends on a manually-seeded secret already existing.

locals {
  # Env shared by the walker and the processor. Each element carries both `value`
  # and `secret_name` (one null) so the list stays a single object type for the
  # dynamic env block. Managed identity is used for the queue at run time; the
  # queue connection string is never exported to the app (only the KEDA scaler).
  base_env = [
    { name = "AZURE_CLIENT_ID", value = var.runtime_identity_client_id, secret_name = null },
    { name = "CLASSIFIER_SOURCE", value = "sharepoint", secret_name = null },
    { name = "CLASSIFIER__DATABASE_URL", value = null, secret_name = "database-url" },
    { name = "CLASSIFIER__QUEUE_NAME", value = var.queue_name, secret_name = null },
    { name = "CLASSIFIER__QUEUE_ACCOUNT_URL", value = var.queue_account_url, secret_name = null },
    { name = "CLASSIFIER__QUEUE_USE_MANAGED_IDENTITY", value = "true", secret_name = null },
    { name = "CLASSIFIER__GRAPH_TENANT_ID", value = var.graph_tenant_id, secret_name = null },
    { name = "CLASSIFIER__GRAPH_CLIENT_ID", value = var.graph_client_id, secret_name = null },
    { name = "CLASSIFIER__GRAPH_CLIENT_SECRET", value = null, secret_name = "graph-client-secret" },
    { name = "CLASSIFIER__WALKER_DRIVE_ID", value = var.walker_drive_id, secret_name = null },
    { name = "CLASSIFIER__WALKER_ROOT_PATH", value = var.walker_root_path, secret_name = null },
    { name = "CLASSIFIER__WALKER_TIME_BUDGET_SECONDS", value = tostring(var.walker_time_budget_seconds), secret_name = null },
  ]

  # Processor = walker env plus the inference provider, its key, the taxonomy file,
  # and the classification knobs.
  processor_env = concat(local.base_env, [
    { name = "CLASSIFIER_PROVIDER", value = "anthropic", secret_name = null },
    { name = "ANTHROPIC_API_KEY", value = null, secret_name = "anthropic-api-key" },
    { name = "CLASSIFIER__PROCESSOR_CATEGORY_FILE", value = "/app/categories.md", secret_name = null },
    { name = "CLASSIFIER_N", value = tostring(var.self_consistency_n), secret_name = null },
    { name = "CLASSIFIER_TEMPERATURE", value = tostring(var.temperature), secret_name = null },
    { name = "CLASSIFIER_CONFIDENCE_THRESHOLD", value = tostring(var.confidence_threshold), secret_name = null },
  ])

  # Migrations need only the database URL.
  migrate_env = [
    { name = "CLASSIFIER__DATABASE_URL", value = null, secret_name = "database-url" },
  ]
}

resource "azurerm_container_app_environment" "main" {
  name                       = var.environment_name
  resource_group_name        = var.resource_group_name
  location                   = var.location
  log_analytics_workspace_id = var.log_analytics_workspace_id
  logs_destination           = "log-analytics"

  tags = var.tags
}

# --- walker: scheduled producer ------------------------------------------------

resource "azurerm_container_app_job" "walker" {
  name                         = "job-walker"
  resource_group_name          = var.resource_group_name
  location                     = var.location
  container_app_environment_id = azurerm_container_app_environment.main.id
  replica_timeout_in_seconds   = var.job_replica_timeout_seconds
  replica_retry_limit          = 1
  tags                         = var.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [var.runtime_identity_id]
  }

  registry {
    server   = var.registry_server
    identity = var.runtime_identity_id
  }

  secret {
    name                = "database-url"
    key_vault_secret_id = var.database_url_secret_id
    identity            = var.runtime_identity_id
  }

  secret {
    name                = "graph-client-secret"
    key_vault_secret_id = var.graph_client_secret_id
    identity            = var.runtime_identity_id
  }

  # Exactly one trigger type. Manual for initial validation (run on demand);
  # switch walker_trigger_mode to "schedule" to activate the cron instead.
  dynamic "manual_trigger_config" {
    for_each = var.walker_trigger_mode == "manual" ? [1] : []
    content {}
  }

  dynamic "schedule_trigger_config" {
    for_each = var.walker_trigger_mode == "schedule" ? [1] : []
    content {
      cron_expression = var.walker_cron
    }
  }

  template {
    container {
      name    = "walker"
      image   = var.image
      cpu     = var.walker_cpu
      memory  = var.walker_memory
      command = ["python", "-m", "walker"]

      dynamic "env" {
        for_each = local.base_env
        content {
          name        = env.value.name
          value       = env.value.value
          secret_name = env.value.secret_name
        }
      }
    }
  }
}

# --- processor: queue-triggered consumer, scales 0 → N on queue depth ----------

resource "azurerm_container_app_job" "processor" {
  name                         = "job-processor"
  resource_group_name          = var.resource_group_name
  location                     = var.location
  container_app_environment_id = azurerm_container_app_environment.main.id
  replica_timeout_in_seconds   = var.job_replica_timeout_seconds
  replica_retry_limit          = 3
  tags                         = var.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [var.runtime_identity_id]
  }

  registry {
    server   = var.registry_server
    identity = var.runtime_identity_id
  }

  secret {
    name                = "database-url"
    key_vault_secret_id = var.database_url_secret_id
    identity            = var.runtime_identity_id
  }

  secret {
    name                = "graph-client-secret"
    key_vault_secret_id = var.graph_client_secret_id
    identity            = var.runtime_identity_id
  }

  secret {
    name                = "anthropic-api-key"
    key_vault_secret_id = var.anthropic_api_key_secret_id
    identity            = var.runtime_identity_id
  }

  # Scaler-only credential: the KEDA azure-queue authentication block supports
  # only secret_name/trigger_parameter (no managed identity), so the scaler reads
  # queue depth via this connection string. The app runtime still uses the runtime
  # identity for actual queue access (CLASSIFIER__QUEUE_USE_MANAGED_IDENTITY).
  secret {
    name  = "queue-connection-string"
    value = var.queue_connection_string
  }

  event_trigger_config {
    parallelism              = 1
    replica_completion_count = 1

    scale {
      min_executions              = 0
      max_executions              = var.processor_max_executions
      polling_interval_in_seconds = 30

      rules {
        name             = "queue-depth"
        custom_rule_type = "azure-queue"
        metadata = {
          accountName = var.storage_account_name
          queueName   = var.queue_name
          queueLength = "1"
        }

        authentication {
          secret_name       = "queue-connection-string"
          trigger_parameter = "connection"
        }
      }
    }
  }

  template {
    container {
      name    = "processor"
      image   = var.image
      cpu     = var.processor_cpu
      memory  = var.processor_memory
      command = ["python", "-m", "processor"]

      dynamic "env" {
        for_each = local.processor_env
        content {
          name        = env.value.name
          value       = env.value.value
          secret_name = env.value.secret_name
        }
      }
    }
  }
}

# --- migrate: manual schema migration (alembic upgrade head) -------------------

resource "azurerm_container_app_job" "migrate" {
  name                         = "job-migrate"
  resource_group_name          = var.resource_group_name
  location                     = var.location
  container_app_environment_id = azurerm_container_app_environment.main.id
  replica_timeout_in_seconds   = var.migrate_replica_timeout_seconds
  replica_retry_limit          = 1
  tags                         = var.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [var.runtime_identity_id]
  }

  registry {
    server   = var.registry_server
    identity = var.runtime_identity_id
  }

  secret {
    name                = "database-url"
    key_vault_secret_id = var.database_url_secret_id
    identity            = var.runtime_identity_id
  }

  manual_trigger_config {}

  template {
    container {
      name    = "migrate"
      image   = var.image
      cpu     = var.migrate_cpu
      memory  = var.migrate_memory
      command = ["alembic", "upgrade", "head"]

      dynamic "env" {
        for_each = local.migrate_env
        content {
          name        = env.value.name
          value       = env.value.value
          secret_name = env.value.secret_name
        }
      }
    }
  }
}
