# Backend config for `terraform init -backend-config=environments/dev.backend.hcl`.
# These values come from the bootstrap/ apply (step 0 of the runbook). The storage
# account name is globally unique — keep it in sync with what bootstrap created.

resource_group_name  = "rg-classifier-tfstate"
storage_account_name = "stclsfrtfstatedev"
container_name       = "tfstate"
key                  = "dev.terraform.tfstate"
