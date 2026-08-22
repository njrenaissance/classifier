output "fqdn" {
  description = "Server fully-qualified domain name."
  value       = azurerm_postgresql_flexible_server.main.fqdn
}

output "server_id" {
  description = "Resource id of the Flexible Server."
  value       = azurerm_postgresql_flexible_server.main.id
}

output "connection_url" {
  description = "SQLAlchemy connection URL (postgresql+psycopg://, sslmode=require). Stored as the database-url Key Vault secret; handed straight to create_engine (src/db.py)."
  value       = "postgresql+psycopg://${var.admin_username}:${random_password.admin.result}@${azurerm_postgresql_flexible_server.main.fqdn}:5432/${var.database_name}?sslmode=require"
  sensitive   = true
}
