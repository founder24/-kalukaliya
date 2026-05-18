/**
 * R2 Asset Serving Logic
 * Handles static asset delivery from Cloudflare R2 bucket
 */

export async function serveAsset(
  key: string, 
  bucket: R2Bucket
): Promise<Response> {
  try {
    const object = await bucket.get(key);
    
    if (!object) {
      return new Response('Asset Not Found', { status: 404 });
    }

    const headers = new Headers();
    object.writeHttpMetadata(headers);
    
    // Set aggressive caching for static assets
    headers.set('Cache-Control', 'public, max-age=31536000, immutable');
    
    // Set content type if not already set
    if (!headers.has('Content-Type')) {
      const ext = key.split('.').pop()?.toLowerCase();
      const mimeTypes: Record<string, string> = {
        'js': 'application/javascript',
        'css': 'text/css',
        'html': 'text/html',
        'json': 'application/json',
        'png': 'image/png',
        'jpg': 'image/jpeg',
        'jpeg': 'image/jpeg',
        'gif': 'image/gif',
        'svg': 'image/svg+xml',
        'ico': 'image/x-icon',
        'woff': 'font/woff',
        'woff2': 'font/woff2',
        'ttf': 'font/ttf',
        'eot': 'application/vnd.ms-fontobject',
      };
      if (ext && mimeTypes[ext]) {
        headers.set('Content-Type', mimeTypes[ext]);
      }
    }

    return new Response(object.body, { 
      status: 200,
      headers 
    });
  } catch (error) {
    console.error('R2 asset serving error:', error);
    return new Response('Internal Server Error', { status: 500 });
  }
}
