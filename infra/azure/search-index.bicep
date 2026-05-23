param location string
param sku string = 'standard'
param semanticRankerEnabled bool = true

resource searchService 'Microsoft.Search/searchServices@2023-11-01' = {
  name: 'srch-syrabit'
  location: location
  sku: {
    name: sku
  }
  properties: {
    replicaCount: 1
    partitionCount: 1
    hostingMode: 'default'
    semanticSearch: semanticRankerEnabled ? 'standard' : 'disabled'
  }
}

output endpoint string = 'https://${searchService.name}.search.windows.net'
output name string = searchService.name
