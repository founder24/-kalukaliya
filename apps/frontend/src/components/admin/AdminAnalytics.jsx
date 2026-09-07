import CommandCenter from './analytics/CommandCenter';

// The staff surface deliberately uses the operational Worker summary only.
// Paid-product, GA4, GCP and prediction reporting remain outside this route.
export default function AdminAnalytics() {
  return <CommandCenter />;
}
