/**
 * API Proxy - Forwards requests to Azure Backend with Header Injection
 */

export async function proxyRequest(
  request: Request, 
  backendUrl: string, 
  env: Env
): Promise<Response> {
  const url = new URL(request.url);
  const targetUrl = `${backendUrl}${url.pathname}${url.search}`;

  // Clone headers and inject Cloudflare-specific headers
  const headers = new Headers(request.headers);
  
  // Inject user identity headers (extracted from JWT by backend)
  const userId = headers.get('X-User-ID');
  const realIp = request.headers.get('CF-Connecting-IP') || 'unknown';
  const cfRayId = request.headers.get('CF-Ray') || '';

  // Add Cloudflare metadata headers
  headers.set('X-Real-IP', realIp);
  headers.set('CF-Ray-ID', cfRayId);
  headers.set('X-Forwarded-Proto', 'https');

  // Remove hop-by-hop headers
  headers.delete('Host');
  headers.delete('Content-Length');

  try {
    const response = await fetch(targetUrl, {
      method: request.method,
      headers: headers,
      body: request.method !== 'GET' && request.method !== 'HEAD' ? request.body : undefined,
    });

    // Clone response to add CORS headers
    const responseHeaders = new Headers(response.headers);
    responseHeaders.set('Access-Control-Allow-Origin', 'https://syrabit.ai');
    
    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: responseHeaders,
    });
  } catch (error) {
    console.error('Proxy error:', error);
    return new Response(JSON.stringify({ 
      error: 'Backend service unavailable',
      details: error instanceof Error ? error.message : 'Unknown error'
    }), { 
      status: 503,
      headers: { 'Content-Type': 'application/json' }
    });
  }
}
