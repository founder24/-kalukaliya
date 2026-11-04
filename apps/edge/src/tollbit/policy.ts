// User-Agent matching selects a policy; it is not proof of crawler identity.
// Training rules take precedence over retrieval matches, including mixed UAs.
export const TRAINING_ONLY_USER_AGENTS = [
  'GPTBot', 'ClaudeBot', 'Claude-Web', 'anthropic-ai', 'CCBot',
  'Google-Extended', 'Applebot-Extended', 'Meta-ExternalAgent',
  'Bytespider', 'Amazonbot', 'Cohere-AI', 'Diffbot',
] as const;

export const AGENT_SITE_USER_AGENTS = [
  'AgentNativeSearchBot', 'AI2Bot', 'AionBot', 'AIWebIndex',
  'Amzn-SearchBot', 'Amzn-User', 'atevesearchsourceurldiscovery',
  'azureai-searchbot', 'Bravebot', 'ChatGPT-User', 'claude-code',
  'Claude-SearchBot', 'Claude-User', 'claude/',
  'cloudflarebrowserrenderingcrawler', 'OAI-SearchBot',
  'PerplexityBot', 'Perplexity-User', 'YouBot',
] as const;

export function matchesAgent(userAgent: string | null, agents: readonly string[]): boolean {
  if (!userAgent || userAgent.length > 2048) return false;
  const normalized = userAgent.toLowerCase();
  return agents.some((agent) => normalized.includes(agent.toLowerCase()));
}
