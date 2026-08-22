output "environment_name" {
  description = "Name of the Container Apps managed environment."
  value       = azurerm_container_app_environment.main.name
}

output "walker_job_name" {
  description = "Walker job name (az containerapp job start -n <this> to run a walk on demand)."
  value       = azurerm_container_app_job.walker.name
}

output "processor_job_name" {
  description = "Processor job name (scales from the queue; start manually to drain on demand)."
  value       = azurerm_container_app_job.processor.name
}

output "migrate_job_name" {
  description = "Migrate job name (az containerapp job start -n <this> runs alembic upgrade head)."
  value       = azurerm_container_app_job.migrate.name
}
