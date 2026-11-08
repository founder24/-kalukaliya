// User-Agent matching selects paid/denied policy; it is not proof of identity.
// Free Search Engine forwarding requires Cloudflare-verified request metadata.
// Training rules take precedence over retrieval matches, including mixed UAs.
export const TRAINING_ONLY_USER_AGENTS = [
  'GPTBot', 'ClaudeBot', 'Claude-Web', 'anthropic-ai', 'CCBot',
  'Google-Extended', 'Applebot-Extended', 'Meta-ExternalAgent',
  'Bytespider', 'Amazonbot', 'Cohere-AI', 'Diffbot',
] as const;

export const OPEN_ACCESS_SEARCH_USER_AGENTS = [
  'Googlebot', 'Bingbot', 'Applebot', 'DuckDuckBot',
] as const;

export const AGENT_SITE_USER_AGENTS = [
  'AgentNativeSearchBot', 'AI2Bot', 'AionBot', 'AIWebIndex',
  'Amzn-SearchBot', 'Amzn-User', 'atevesearchsourceurldiscovery',
  'azureai-searchbot', 'Bravebot', 'ChatGPT-User', 'claude-code',
  'Claude-SearchBot', 'Claude-User', 'claude/',
  'cloudflarebrowserrenderingcrawler', 'OAI-SearchBot',
  'PerplexityBot', 'Perplexity-User', 'YouBot',
  // Publisher-supplied TollBit candidates. Unless a token is in the
  // training-only list above, route it to the Agent Site for paid handling.
  'AgentDataBot', 'AgentTimes', 'AGI Agent', 'AI2Bot-DeepResearchEval',
  'aiHitBot', 'AmazonBuyForMe', 'Andibot', 'Anomura', 'ApifyBot',
  'ApifyWebsiteContentCrawler', 'atlassian-bot', 'Awario', 'bedrockbot',
  'bigsur.ai', 'BixelBot', 'Brightbot', 'Brightbot 1.0', 'BuddyBot',
  'CartAI', 'CartAI Agentic Commerce', 'Channel3Bot', 'ChatGLM-Spider',
  'Claude-Code', 'CledaraBot', 'CloudVertexBot', 'Code', 'Codex',
  'Cotoyogi', 'CragCrawler', 'Crawl4AI', 'Crawlspace', 'Cursor',
  'Datenbank Crawler', 'DeepSeekBot', 'Devin', 'Dot', 'DoubaoBot',
  'Echobot Bot', 'EchoboxBot', 'ElevenlabsBot', 'ElevenlabsCrawler',
  'ERNIEBot', 'ExaBot', 'FacebookBot', 'facebookexternalhit',
  'Factset_spyderbot', 'FriendlyCrawler', 'GeistHaus-PageFetcher',
  'Google-CloudVertexBot', 'Google-Firebase', 'Google-Gemini-CLI',
  'GoogleOther', 'GoogleOther-Image', 'GoogleOther-Video', 'Grok Bot',
  'GrokAppAndroid', 'GrokBot', 'HarkBot', 'HenkBot', 'Hunyuan',
  'ICC-Crawler', 'ImagesiftBot', 'imageSpider', 'Instinct',
  'ISSCyberRiskCrawler', 'kagi-fetcher', 'Kangaroo Bot', 'KangarooBot',
  'KeenableBot', 'Kimi-Agent', 'KimiBot', 'KlaviyoAIBot',
  'KunatoCrawler', 'LCC', 'Lightpanda', 'Linguee Bot', 'LinkupBot',
  'meta-externalfetcher', 'meta-webindexer', 'MoonshotBot',
  'Mozilla-Tabstack', 'Muse', 'MyCentralAIScraperBot', 'NagetBot',
  'nbot', 'netEstate Imprint Crawler', 'newsai', 'OAI-AdsBot',
  'omgili', 'omgilibot', 'OpenAI', 'opencode', 'PanguBot',
  'Panscient', 'panscient.com', 'Payhawk', 'Payhawk Invoice Fetching Agent',
  'Poggio-Citations', 'Poseidon Research Crawler', 'qodercli',
  'QualifiedBot', 'QueritBot', 'QuillBot', 'quillbot.com', 'QwenBot',
  'Reflectionbot', 'SBIntuitionsBot', 'Scrapy', 'ScryBot',
  'SemrushBot-OCOB', 'SemrushBot-SWA', 'ShapBot', 'Sidetrade',
  'Sidetrade indexer bot', 'Spider', 'Terra Cotta', 'TerraCotta',
  'Thinkbot', 'TikTokSpider', 'Timpibot', 'TongyiBot', 'Trae',
  'TwinAgent', 'UseAI', 'VelenPublicWebCrawler', 'WARDBot',
  'Webzio-Extended', 'wpbot', 'WRTNBot', 'xAI-Grok', 'YaK',
  'YiBot', 'YiyanBot', 'ZanistaBot',
] as const;

function isWordCharacter(character: string | undefined): boolean {
  return character !== undefined && /[\p{L}\p{N}]/u.test(character);
}

export function matchesAgent(userAgent: string | null, agents: readonly string[]): boolean {
  if (!userAgent || userAgent.length > 2048) return false;
  const normalized = userAgent.toLowerCase();
  return agents.some((agent) => {
    const needle = agent.toLowerCase();
    if (!needle) return false;

    let index = normalized.indexOf(needle);
    while (index !== -1) {
      const before = index > 0 ? normalized[index - 1] : undefined;
      const end = index + needle.length;
      const after = end < normalized.length ? normalized[end] : undefined;
      const leftBoundary = !isWordCharacter(needle[0]) || !isWordCharacter(before);
      const rightBoundary =
        !isWordCharacter(needle[needle.length - 1]) || !isWordCharacter(after);

      if (leftBoundary && rightBoundary) return true;
      index = normalized.indexOf(needle, index + 1);
    }

    return false;
  });
}
