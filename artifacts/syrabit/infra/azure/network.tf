# infra/azure/network.tf
#
# Phase 1c — Azure landing zone (Task #329).
#
# VNet baseline for the Azure cron + observability tier. Sized for
# Container Apps Jobs (cron) and the App Insights / Log Analytics sink,
# NOT for synchronous API traffic (the sync API tier lives on Digital
# Ocean per ADR-0001).
#
# Layout
# ──────
# • /16 VNet in the primary region (centralindia).
# • /23 subnet for the Container Apps environment that runs the cron
#   jobs — Azure requires at minimum a /23 (512 addresses) for an ACA
#   environment; the cron tier rarely scales past a handful of replicas
#   so the rest of the address space is reserved for future use.
# • /24 subnet for private endpoints (Key Vault, ACR, App Insights
#   ingestion when private-link is enabled in Phase 5).
# • Baseline NSGs:
#     - cron-jobs:        no inbound; outbound 443 + DNS only.
#     - private-endpoints: 443 inbound from cron-jobs subnet; no outbound.
#
# Service endpoints for Key Vault and ACR are enabled on the cron-jobs
# subnet so jobs can reach those services over the Microsoft backbone
# without paying egress to the public internet.

resource "azurerm_virtual_network" "cron_obs" {
  name                = "${local.lz_project}-cron-obs-vnet"
  location            = azurerm_resource_group.cron_obs.location
  resource_group_name = azurerm_resource_group.cron_obs.name
  address_space       = ["10.50.0.0/16"]

  tags = local.lz_common_tags
}

# ─── Subnets ─────────────────────────────────────────────────────────────────

resource "azurerm_subnet" "cron_jobs" {
  name                 = "${local.lz_project}-cron-jobs-subnet"
  resource_group_name  = azurerm_resource_group.cron_obs.name
  virtual_network_name = azurerm_virtual_network.cron_obs.name
  address_prefixes     = ["10.50.0.0/23"]

  service_endpoints = [
    "Microsoft.KeyVault",
    "Microsoft.ContainerRegistry",
    "Microsoft.Storage",
  ]

  # Container Apps environments require this delegation.
  delegation {
    name = "container-apps-delegation"
    service_delegation {
      name = "Microsoft.App/environments"
      actions = [
        "Microsoft.Network/virtualNetworks/subnets/join/action",
      ]
    }
  }
}

resource "azurerm_subnet" "private_endpoints" {
  name                 = "${local.lz_project}-private-endpoints-subnet"
  resource_group_name  = azurerm_resource_group.cron_obs.name
  virtual_network_name = azurerm_virtual_network.cron_obs.name
  address_prefixes     = ["10.50.4.0/24"]

  # Disable network policies on the subnet so private endpoints can be
  # created here in Phase 5 (azurerm 3.x boolean form; the string-form
  # `private_endpoint_network_policies = "Disabled"` is the post-3.41
  # successor but the boolean is still honoured under our >= 3.90 pin
  # and is the form the provider documents in upgrade guides).
  private_endpoint_network_policies_enabled = false
}

# ─── Baseline NSGs ──────────────────────────────────────────────────────────

resource "azurerm_network_security_group" "cron_jobs" {
  name                = "${local.lz_project}-cron-jobs-nsg"
  location            = azurerm_resource_group.cron_obs.location
  resource_group_name = azurerm_resource_group.cron_obs.name

  # No explicit inbound rules — cron jobs do not accept connections.
  # Default deny-inbound applies.

  security_rule {
    name                       = "AllowOutboundHttps"
    priority                   = 100
    direction                  = "Outbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "443"
    source_address_prefix      = "*"
    destination_address_prefix = "Internet"
  }

  security_rule {
    name                       = "AllowOutboundDnsUdp"
    priority                   = 110
    direction                  = "Outbound"
    access                     = "Allow"
    protocol                   = "Udp"
    source_port_range          = "*"
    destination_port_range     = "53"
    source_address_prefix      = "*"
    destination_address_prefix = "*"
  }

  security_rule {
    name                       = "AllowOutboundDnsTcp"
    priority                   = 111
    direction                  = "Outbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "53"
    source_address_prefix      = "*"
    destination_address_prefix = "*"
  }

  security_rule {
    name                       = "DenyAllOutbound"
    priority                   = 4096
    direction                  = "Outbound"
    access                     = "Deny"
    protocol                   = "*"
    source_port_range          = "*"
    destination_port_range     = "*"
    source_address_prefix      = "*"
    destination_address_prefix = "*"
  }

  tags = local.lz_common_tags
}

resource "azurerm_network_security_group" "private_endpoints" {
  name                = "${local.lz_project}-private-endpoints-nsg"
  location            = azurerm_resource_group.cron_obs.location
  resource_group_name = azurerm_resource_group.cron_obs.name

  security_rule {
    name                       = "AllowHttpsFromCronJobs"
    priority                   = 100
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "443"
    source_address_prefix      = "10.50.0.0/23"
    destination_address_prefix = "*"
  }

  tags = local.lz_common_tags
}

resource "azurerm_subnet_network_security_group_association" "cron_jobs" {
  subnet_id                 = azurerm_subnet.cron_jobs.id
  network_security_group_id = azurerm_network_security_group.cron_jobs.id
}

resource "azurerm_subnet_network_security_group_association" "private_endpoints" {
  subnet_id                 = azurerm_subnet.private_endpoints.id
  network_security_group_id = azurerm_network_security_group.private_endpoints.id
}

# ─── Outputs (consumed by downstream cron-jobs Terraform in Phase 4) ─────────

output "cron_obs_vnet_id" {
  value       = azurerm_virtual_network.cron_obs.id
  description = "VNet ID for the cron + observability tier."
}

output "cron_jobs_subnet_id" {
  value       = azurerm_subnet.cron_jobs.id
  description = "Subnet ID for the Container Apps environment that hosts cron jobs."
}

output "private_endpoints_subnet_id" {
  value       = azurerm_subnet.private_endpoints.id
  description = "Subnet ID for Key Vault / ACR / App Insights private endpoints."
}
