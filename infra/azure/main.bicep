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

module sharedResources './shared-resources.bicep' = {
  scope: resourceGroup
  name: 'syrabit-shared'
  params: {
    location: location
    resourceGroupId: resourceGroup.id
  }
}

output searchEndpoint string = searchService.outputs.endpoint
output containerAppUrl string = containerApp.outputs.url
output storageAccountName string = sharedResources.outputs.storageAccountName
output keyVaultUri string = sharedResources.outputs.keyVaultUri
