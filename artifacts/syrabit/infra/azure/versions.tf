# infra/azure/versions.tf
#
# Provider/version pins for the Azure landing zone Terraform root.
# The existing front-door.tf pinned only `azurerm`; the Phase 1c work
# adds `azuread` (federated credential + service principal) so the pin
# is consolidated here. front-door.tf and cosmos-db-cache.tf keep
# their own `terraform { required_providers { azurerm = ... } }`
# blocks for backward compatibility — Terraform merges identical
# version constraints across files cleanly.

terraform {
  required_version = ">= 1.6"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = ">= 3.90"
    }
    azuread = {
      source  = "hashicorp/azuread"
      version = ">= 2.47"
    }
    null = {
      source  = "hashicorp/null"
      version = ">= 3.2"
    }
  }
}

provider "azuread" {}
