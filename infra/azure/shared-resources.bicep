param location string
param resourceGroupId string

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-01-01' = {
  name: 'syrabitassets${uniqueString(resourceGroupId)}'
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
    tenantId: tenant().tenantId
    enableRbacAuthorization: true
    sku: {
      family: 'A'
      name: 'standard'
    }
  }
}

output storageAccountName string = storageAccount.name
output keyVaultUri string = keyVault.properties.vaultUri
