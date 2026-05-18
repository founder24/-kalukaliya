import { turnstileVerify } from './middleware/bot';
import { proxyRequest } from './routes/api-proxy';

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);

    // 1. CORS Handling
    if (request.method === 'OPTIONS') {
      return new Response(null, {
        headers: {
          'Access-Control-Allow-Origin': 'https://syrabit.ai',
          'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
          'Access-Control-Allow-Headers': 'Content-Type, Authorization, CF-Turnstile-Response',
        },
      });
    }

    // 2. Turnstile Bot Protection (Only for Chat/Login endpoints)
    if (url.pathname.startsWith('/api/v1/chat') || url.pathname.startsWith('/api/v1/auth')) {
      const turnstileToken = request.headers.get('CF-Turnstile-Response');
      if (!turnstileToken) {
        return new Response(JSON.stringify({ error: 'Bot verification required' }), { 
          status: 403,
          headers: { 'Content-Type': 'application/json' }
        });
      }
      
      const isValid = await turnstileVerify(turnstileToken, env.CF_TURNSTILE_SECRET);
      if (!isValid) {
        return new Response(JSON.stringify({ error: 'Bot verification failed' }), { 
          status: 403,
          headers: { 'Content-Type': 'application/json' }
        });
      }
    }

    // 3. Routing
    if (url.pathname.startsWith('/api/')) {
      // Proxy to Azure Backend
      return await proxyRequest(request, env.AZURE_BACKEND_URL, env);
    } 

    if (url.pathname.startsWith('/assets/')) {
      // Serve from R2
      const key = url.pathname.replace('/assets/', '');
      const object = await env.R2_BUCKET.get(key);
      if (!object) return new Response('Not Found', { status: 404 });
      
      const headers = new Headers();
      object.writeHttpMetadata(headers);
      headers.set('Cache-Control', 'public, max-age=31536000');
      
      return new Response(object.body, { headers });
    }

    return new Response('Not Found', { status: 404 });
  },
};
