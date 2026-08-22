# Azure Database for PostgreSQL Flexible Server (ADR-0013). Burstable tier, public
# endpoint with an Azure-services firewall rule (no VNet/private endpoint in this
# pass — a documented hardening follow-up). Password auth: the admin password is
# generated here and never committed as plaintext; the assembled connection URL is
# the module's `connection_url` output, stored as the database-url Key Vault secret.
#
# `sslmode=require` is appended because Flexible Server enforces TLS; the psycopg
# (v3) driver honours it. The URL scheme matches the app/local stack
# (postgresql+psycopg://, see infra/docker-compose.yml).

resource "random_password" "admin" {
  length = 32
  # URL-unreserved specials only, so the password is safe in the connection URL's
  # userinfo without percent-encoding.
  special          = true
  override_special = "-_.~"
}

resource "azurerm_postgresql_flexible_server" "main" {
  name                = var.name
  resource_group_name = var.resource_group_name
  location            = var.location
  version             = var.postgres_version

  administrator_login    = var.admin_username
  administrator_password = random_password.admin.result

  sku_name   = var.sku_name
  storage_mb = var.storage_mb

  # No high-availability / zone redundancy on Burstable; keep the first cut lean.
  zone = "1"

  tags = var.tags
}

resource "azurerm_postgresql_flexible_server_database" "app" {
  name      = var.database_name
  server_id = azurerm_postgresql_flexible_server.main.id
  charset   = "UTF8"
  collation = "en_US.utf8"
}

# Allow other Azure services (the ACA jobs) to reach the server. The 0.0.0.0
# start/end sentinel is Azure's "Allow Azure services" rule, matching ADR-0013's
# Burstable + Azure-services-firewall posture.
resource "azurerm_postgresql_flexible_server_firewall_rule" "azure_services" {
  name             = "AllowAzureServices"
  server_id        = azurerm_postgresql_flexible_server.main.id
  start_ip_address = "0.0.0.0"
  end_ip_address   = "0.0.0.0"
}
