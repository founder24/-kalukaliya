targetScope = 'subscription'

param resourceGroupName string = 'rg-syrabit-prod'
param location string = 'centralindia'

resource resourceGroup 'Microsoft.Resources/resourceGroups@2023-07-01' = {
  name: resourceGroupName
  location: location
}

module searchService './search-index.bicep' = {
  scope: resourceGroup
  name: 'syrabit-search'
  params: {
    location: location
    sku: 'standard'
    semanticRankerEnabled: true
  }
}

module containerApp './container-app.bicep' = {
  scope: resourceGroup
  name: 'syrabit-api'
  params: {
    location: location
    containerAppName: 'ca-syrabit-api'
  }
}

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-01-01' = {
  name: 'syrabitassets${uniqueString(resourceGroup.id)}'
  location: location
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    accessTier: 'Hot'
  }
}

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: 'syrabit-kv'
  location: location
  properties: {
    tenantId: subscription().tenantId
    accessPolicies: []
    sku: {
      family: 'A'
      name: 'standard'
    }
  }
}

output searchEndpoint string = searchService.outputs.endpoint
output containerAppUrl string = containerApp.outputs.url
