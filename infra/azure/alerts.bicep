param location string
param containerAppId string
param actionGroupEmail string = 'alerts@syrabit.ai'

// Action Group - email notification
resource actionGroup 'Microsoft.Insights/actionGroups@2023-01-01' = {
  name: 'ag-syrabit-critical'
  location: 'global'
  properties: {
    groupShortName: 'SyrabitAlrt'
    enabled: true
    emailReceivers: [
      {
        name: 'OpsTeam'
        emailAddress: actionGroupEmail
        useCommonAlertSchema: true
      }
    ]
  }
}

// Alert 1: HTTP 5xx error rate > 5% over 5 minutes (Severity 1 - Critical)
resource http5xxAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: 'alert-http-5xx-rate'
  location: 'global'
  properties: {
    description: 'HTTP 5xx error rate exceeds 5% over 5 minutes'
    severity: 1
    enabled: true
    scopes: [containerAppId]
    evaluationFrequency: 'PT1M'
    windowSize: 'PT5M'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'High5xxRate'
          metricName: 'Requests'
          metricNamespace: 'Microsoft.App/containerApps'
          operator: 'GreaterThan'
          threshold: 5
          timeAggregation: 'Average'
          dimensions: [
            {
              name: 'statusCodeCategory'
              operator: 'Include'
              values: ['5xx']
            }
          ]
          criterionType: 'StaticThresholdCriterion'
        }
      ]
    }
    actions: [
      {
        actionGroupId: actionGroup.id
      }
    ]
  }
}

// Alert 2: Response time p95 > 3 seconds over 5 minutes (Severity 2 - Warning)
resource responseTimeAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: 'alert-response-time-p95'
  location: 'global'
  properties: {
    description: 'Response time p95 exceeds 3 seconds over 5 minutes'
    severity: 2
    enabled: true
    scopes: [containerAppId]
    evaluationFrequency: 'PT1M'
    windowSize: 'PT5M'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'HighP95Latency'
          metricName: 'RequestDuration'
          metricNamespace: 'Microsoft.App/containerApps'
          operator: 'GreaterThan'
          threshold: 3000
          timeAggregation: 'Average'
          criterionType: 'StaticThresholdCriterion'
        }
      ]
    }
    actions: [
      {
        actionGroupId: actionGroup.id
      }
    ]
  }
}

// Alert 3: Container app restarts > 3 in 10 minutes (Severity 1 - Critical)
resource restartAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: 'alert-container-restarts'
  location: 'global'
  properties: {
    description: 'Container app restarts exceed 3 in 10 minutes'
    severity: 1
    enabled: true
    scopes: [containerAppId]
    evaluationFrequency: 'PT1M'
    windowSize: 'PT10M'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'HighRestartCount'
          metricName: 'RestartCount'
          metricNamespace: 'Microsoft.App/containerApps'
          operator: 'GreaterThan'
          threshold: 3
          timeAggregation: 'Total'
          criterionType: 'StaticThresholdCriterion'
        }
      ]
    }
    actions: [
      {
        actionGroupId: actionGroup.id
      }
    ]
  }
}

// Alert 4: Memory usage > 80% (Severity 2 - Warning)
resource memoryAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: 'alert-memory-usage'
  location: 'global'
  properties: {
    description: 'Container memory usage exceeds 80%'
    severity: 2
    enabled: true
    scopes: [containerAppId]
    evaluationFrequency: 'PT1M'
    windowSize: 'PT5M'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'HighMemoryUsage'
          metricName: 'UsageNanoCores'
          metricNamespace: 'Microsoft.App/containerApps'
          operator: 'GreaterThan'
          threshold: 80
          timeAggregation: 'Average'
          criterionType: 'StaticThresholdCriterion'
        }
      ]
    }
    actions: [
      {
        actionGroupId: actionGroup.id
      }
    ]
  }
}
