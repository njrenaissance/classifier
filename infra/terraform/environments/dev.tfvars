# dev environment inputs. Non-secret only (subscription/region/naming); no
# credentials live here — the DB password is generated, and the Anthropic/Graph
# secrets are seeded into Key Vault out of band (PR2). Safe to commit.

subscription_id = "2458052a-3cc8-43e3-a53b-e10df34a44d6"
location        = "eastus2"
environment     = "dev"

# name_prefix defaults to "classifier"; queue_name and Postgres sizing use the
# module defaults (Burstable B_Standard_B1ms, 32 GB). Override here if needed.
