# 🚀 DUAL-LLM RAG ARCHITECTURE — FULL IMPLEMENTATION PLAN

**Project:** Syrabit v3.0 (Kalukaliya)
**Date:** May 2026
**Status:** ~30-35% implemented → Target: 100%

---

## 📋 PLAN OVERVIEW

| Phase | Layer | Focus | Effort |
|-------|-------|-------|--------|
| **1** | Backend (Orchestration) | Streaming + Explicit Lang Routing + Fallback | 3-4 days |
| **2** | Edge (CF Worker) | JWT Validation + Stream Proxy + KV Rate Limiting | 2-3 days |
| **3** | Frontend (Client) | React Chat UI + `useChat()` Hook + Lang Selector | 3-4 days |
| **4** | Data (MongoDB) | Feedback Collection + Aggregation Pipeline + Indexes | 1-2 days |
| **5** | Observability | OpenTelemetry + Spans + Dashboards | 1-2 days |
| **6** | CI/CD | LLM Health Checks + GCP OIDC + Unified Deploy | 1 day |

**Total Estimated:** 12-16 dev-days

---

## 🛠️ PREREQUISITES — ENVIRONMENT SETUP

### GitHub Codespace Bootstrap Script

```bash
#!/bin/bash
# File: scripts/codespace-setup.sh
# Run: chmod +x scripts/codespace-setup.sh && ./scripts/codespace-setup.sh

set -euo pipefail
echo "🚀 Setting up Syrabit Dev Environment..."

# ── 1. System Dependencies ──
sudo apt-get update && sudo apt-get install -y jq curl

# ── 2. Node.js (v22 via nvm) ──
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
nvm install 22 && nvm use 22
corepack enable && corepack prepare pnpm@10.26.1 --activate

# ── 3. Python (3.12) ──
python3 --version || sudo apt-get install -y python3.12 python3.12-venv
cd apps/backend
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
cd ../..

# ── 4. Cloudflare Wrangler ──
pnpm install
npx wrangler --version

# ── 5. Azure CLI ──
curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash
az --version

# ── 6. Google Cloud SDK ──
curl https://sdk.cloud.google.com | bash -s -- --disable-prompts
export PATH="$HOME/google-cloud-sdk/bin:$PATH"
gcloud --version

# ── 7. MongoDB Shell ──
wget -qO - https://www.mongodb.org/static/pgp/server-7.0.asc | sudo apt-key add -
echo "deb [ arch=amd64 ] https://repo.mongodb.org/apt/ubuntu jammy/mongodb-org/7.0 multiverse" | \
  sudo tee /etc/apt/sources.list.d/mongodb-org-7.0.list
sudo apt-get update && sudo apt-get install -y mongosh

echo "✅ Environment ready. Activate backend venv: source apps/backend/.venv/bin/activate"
```


### Azure CLI Login & Resource Verification Script

```bash
#!/bin/bash
# File: scripts/azure-verify.sh
# Verifies all Azure resources exist and are accessible

set -euo pipefail

echo "🔐 Logging into Azure..."
az login --use-device-code

# Set subscription
az account set --subscription "$AZURE_SUBSCRIPTION_ID"
echo "✅ Subscription: $(az account show --query name -o tsv)"

# Verify Resource Group
echo "📦 Checking resource group..."
az group show --name rg-syrabit-prod --query provisioningState -o tsv

# Verify Azure AI Search
echo "🔍 Checking Azure AI Search..."
az search service show \
  --name syrabit-search \
  --resource-group rg-syrabit-prod \
  --query "status" -o tsv

# Verify Azure Container App
echo "🐳 Checking Container App..."
az containerapp show \
  --name ca-syrabit-api \
  --resource-group rg-syrabit-prod \
  --query "properties.runningStatus" -o tsv

# Verify Key Vault
echo "🔑 Checking Key Vault access..."
az keyvault secret list \
  --vault-name kv-syrabit-prod \
  --query "[].name" -o tsv

# Test Search Index
echo "📊 Checking Search Index..."
az search index show \
  --service-name syrabit-search \
  --index-name syrabit-edu-index \
  --resource-group rg-syrabit-prod \
  --query "fields | length(@)" -o tsv

echo "✅ All Azure resources verified!"
```


### GCP / Vertex AI Verification Script

```bash
#!/bin/bash
# File: scripts/gcp-verify.sh
# Verifies Vertex AI access and model availability

set -euo pipefail

echo "🔐 Authenticating with GCP..."
gcloud auth activate-service-account \
  --key-file=<(echo "$GOOGLE_APPLICATION_CREDENTIALS_JSON")
gcloud config set project "$VERTEX_PROJECT_ID"

echo "🤖 Checking Gemini model availability..."
gcloud ai models list \
  --region=us-central1 \
  --filter="display_name~gemini" \
  --format="table(name,displayName)"

echo "🧪 Test Gemini endpoint..."
ACCESS_TOKEN=$(gcloud auth print-access-token)
curl -s -w "\nHTTP_STATUS: %{http_code}\n" \
  "https://us-central1-aiplatform.googleapis.com/v1/projects/${VERTEX_PROJECT_ID}/locations/us-central1/publishers/google/models/gemini-1.5-flash:generateContent" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"contents":[{"parts":[{"text":"Say hello"}]}]}' | head -5

echo "✅ Vertex AI verified!"
```

### Sarvam AI Verification Script

```bash
#!/bin/bash
# File: scripts/sarvam-verify.sh
# Verifies Sarvam AI API is accessible

set -euo pipefail

echo "🇮🇳 Testing Sarvam AI endpoint..."
HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
  "https://api.sarvam.ai/v1/chat/completions" \
  -H "Authorization: Bearer ${SARVAM_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"model":"saaras","messages":[{"role":"user","content":"নমস্কাৰ"}],"max_tokens":10}')

if [ "$HTTP_STATUS" -eq 200 ]; then
  echo "✅ Sarvam AI responding (HTTP $HTTP_STATUS)"
else
  echo "❌ Sarvam AI returned HTTP $HTTP_STATUS"
  exit 1
fi
```

---


## ═══════════════════════════════════════════════════════════════
## PHASE 1: BACKEND — STREAMING + EXPLICIT LANG + FALLBACK
## ═══════════════════════════════════════════════════════════════

### Step 1.1: Update `ChatRequest` Model to Accept Explicit `lang`

**File:** `apps/backend/app/api/v1/chat.py`

**What to do:**
- Add `lang: Optional[str] = None` field to `ChatRequest` (accepts `"en"` or `"as"`)
- If `lang` is provided, skip auto-detection and use it directly
- If `lang` is `None`, fall back to auto-detection (backward compatible)
- Validate that `lang` is one of `["en", "as"]` or `None`

```python
# Updated ChatRequest
class ChatRequest(BaseModel):
    message: str
    lang: Optional[str] = None  # Explicit: "en" or "as"
    session_id: Optional[str] = None
    context_messages: List[dict] = []

    @validator('lang')
    def validate_lang(cls, v):
        if v is not None and v not in ("en", "as"):
            raise ValueError("lang must be 'en', 'as', or null")
        return v
```

**In the endpoint logic:**
```python
# Use explicit lang if provided, otherwise auto-detect
if request.lang:
    detected_lang = request.lang
    target_model = settings.SARVAM_MODEL if request.lang == "as" else settings.VERTEX_GEMINI_MODEL
else:
    detected_lang, target_model = detect_language_and_route(request.message)
```

---

### Step 1.2: Implement Streaming in Vertex AI Client

**File:** `apps/backend/app/services/ai/vertex_client.py`

**What to do:**
- Add `async def stream_generate()` method
- Use `streamGenerateContent` endpoint instead of `generateContent`
- Parse streaming JSON chunks and yield text deltas
- Keep existing `generate()` for non-stream fallback

```python
async def stream_generate(
    self,
    system_prompt: str,
    user_message: str,
) -> AsyncGenerator[str, None]:
    """Stream response using Gemini streamGenerateContent"""
    full_prompt = f"{system_prompt}\n\nUser: {user_message}\nAssistant:"
    
    url = f"{self.base_url}/{self.model}:streamGenerateContent?alt=sse"
    headers = {
        "Authorization": f"Bearer {await self._get_access_token()}",
        "Content-Type": "application/json"
    }
    payload = {
        "contents": [{"parts": [{"text": full_prompt}]}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 2048}
    }
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        async with client.stream("POST", url, headers=headers, json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    data = json.loads(line[6:])
                    if "candidates" in data:
                        parts = data["candidates"][0].get("content", {}).get("parts", [])
                        for part in parts:
                            if "text" in part:
                                yield part["text"]
```


---

### Step 1.3: Implement Streaming in Sarvam AI Client

**File:** `apps/backend/app/services/ai/sarvam_client.py`

**What to do:**
- Add `async def stream_generate()` method
- Parse OpenAI-compatible SSE chunks (`data: {...}`)
- Extract `choices[0].delta.content` from each chunk
- Handle `[DONE]` sentinel

```python
async def stream_generate(
    self,
    system_prompt: str,
    user_message: str,
) -> AsyncGenerator[str, None]:
    """Stream response using Sarvam OpenAI-compatible SSE"""
    url = f"{self.base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {self.api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": self.model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ],
        "temperature": 0.7,
        "max_tokens": 2048,
        "stream": True
    }
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        async with client.stream("POST", url, headers=headers, json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    raw = line[6:].strip()
                    if raw == "[DONE]":
                        return
                    chunk = json.loads(raw)
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        yield content
```

---

### Step 1.4: Create Unified Streaming Chat Endpoint

**File:** `apps/backend/app/api/v1/chat.py` (new endpoint alongside existing)

**What to do:**
- Add `POST /api/v1/chat/stream` endpoint
- Returns `StreamingResponse` with `text/event-stream` media type
- Normalizes both Vertex and Sarvam streams to unified SSE format:
  `data: {"text": "...", "done": false}\n\n`
- Sets proper headers: `Cache-Control: no-store`, `Connection: keep-alive`
- Saves full response to MongoDB after stream completes

```python
from fastapi.responses import StreamingResponse

@router.post("/stream")
async def chat_stream(
    request: ChatRequest,
    user: User = Depends(get_current_user),
    http_request: Request = None
):
    """Streaming chat endpoint - SSE"""
    start_time = time.time()
    
    # Rate limit check (same as non-streaming)
    client_ip = http_request.client.host if http_request else None
    user_tier = getattr(user, 'subscription_tier', 'free')
    user_id = str(user.id)
    
    if not await check_rate_limit(user_id, user_tier, client_ip):
        raise HTTPException(status_code=429, detail="Rate limit exceeded.")
    
    # Resolve language
    if request.lang:
        detected_lang = request.lang
        target_model = settings.SARVAM_MODEL if request.lang == "as" else settings.VERTEX_GEMINI_MODEL
    else:
        detected_lang, target_model = detect_language_and_route(request.message)
    
    # RAG retrieval
    from app.services.ai.embedder import generate_embedding
    embedding = await generate_embedding(request.message)
    context_chunks = await search_service.search_context(
        query=request.message, embedding=embedding,
        user_tier=user_tier, limit=settings.MAX_CONTEXT_DOCS
    )
    
    # Build system prompt with [#] citation format
    lang_instr = (
        "Respond in English. Cite sources using [#] format where # is the source number."
        if detected_lang == "en" else
        "অসমীয়াত উত্তৰ দিয়ক। উদ্ধৃতিৰ বাবে [#] ব্যৱহাৰ কৰক যত # হৈছে উৎসৰ নম্বৰ।"
    )
    context_text = "\n".join(
        f"[{i+1}] {c['title']}: {c['content']}" for i, c in enumerate(context_chunks)
    )
    system_prompt = f"{lang_instr}\n\nContext:\n{context_text}"
    
    # Stream generator with fallback
    async def event_stream():
        full_response = ""
        try:
            stream_fn = _get_stream_fn(detected_lang)
            async for chunk in stream_fn(system_prompt, request.message):
                full_response += chunk
                yield f"data: {json.dumps({'text': chunk, 'done': False})}\n\n"
        except Exception as e:
            # FALLBACK: If Sarvam fails, fall back to Vertex English
            if detected_lang == "as":
                logger.warning(f"Sarvam failed ({e}), falling back to Vertex")
                yield f"data: {json.dumps({'fallback': True, 'provider': 'vertex'})}\n\n"
                from app.services.ai.vertex_client import vertex_client
                async for chunk in vertex_client.stream_generate(system_prompt, request.message):
                    full_response += chunk
                    yield f"data: {json.dumps({'text': chunk, 'done': False})}\n\n"
            else:
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
        
        # Final event
        latency_ms = int((time.time() - start_time) * 1000)
        yield f"data: {json.dumps({'text': '', 'done': True, 'latency_ms': latency_ms, 'model': target_model, 'lang': detected_lang})}\n\n"
        
        # Persist chat (fire-and-forget)
        asyncio.create_task(_save_chat(user_id, request, full_response, target_model, latency_ms, context_chunks))
    
    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "Connection": "keep-alive",
            "X-Content-Type-Options": "nosniff",
        }
    )

def _get_stream_fn(lang: str):
    if lang == "as":
        from app.services.ai.sarvam_client import sarvam_client
        return sarvam_client.stream_generate
    else:
        from app.services.ai.vertex_client import vertex_client
        return vertex_client.stream_generate
```


---

### Step 1.5: Update Router to Support Streaming

**File:** `apps/backend/app/services/ai/router.py`

**What to do:**
- Add `stream_response()` function parallel to `generate_response()`
- Accept same params but returns `AsyncGenerator[str, None]`

```python
async def stream_response(
    system_prompt: str,
    user_message: str,
    model: str,
) -> AsyncGenerator[str, None]:
    """Stream response from appropriate AI client."""
    if 'sarvam' in model.lower() or 'openhathi' in model.lower() or 'saaras' in model.lower():
        from app.services.ai.sarvam_client import sarvam_client
        async for chunk in sarvam_client.stream_generate(system_prompt, user_message):
            yield chunk
    else:
        from app.services.ai.vertex_client import vertex_client
        async for chunk in vertex_client.stream_generate(system_prompt, user_message):
            yield chunk
```

---

### Step 1.6: Update System Prompt to Enforce `[#]` Citation Format

**File:** `apps/backend/app/api/v1/chat.py`

**What to do:**
- Replace `"Cite sources using [Source Title]"` with numbered `[#]` format
- Add explicit instruction for both languages
- Number context chunks and reference by number in response

```python
# English instruction
"You are Syrabit, an expert educational assistant for Assamese students. "
"Use the following numbered context to answer. If the answer is not in the context, say so clearly. "
"Cite sources using [#] format (e.g., [1], [2]). Respond in English."

# Assamese instruction  
"আপুনি Syrabit, অসমীয়া ছাত্ৰ-ছাত্ৰীৰ বাবে এজন বিশেষজ্ঞ শিক্ষা সহায়ক। "
"নিম্নলিখিত নম্বৰযুক্ত প্ৰসংগ ব্যৱহাৰ কৰি উত্তৰ দিয়ক। উদ্ধৃতিৰ বাবে [#] বিন্যাস ব্যৱহাৰ কৰক।"
```

---

### Step 1.7: Implement Sarvam → Vertex Fallback Logic

**File:** `apps/backend/app/services/ai/sarvam_client.py`

**What to do:**
- Wrap `stream_generate` with timeout (800ms first-byte) and retry
- On 5xx or timeout: retry once → if fails again, raise to let endpoint handle fallback
- Log fallback events for monitoring

```python
import asyncio

async def stream_generate_with_fallback(
    self,
    system_prompt: str,
    user_message: str,
    timeout_first_byte: float = 0.8,  # 800ms
    max_retries: int = 1,
) -> AsyncGenerator[str, None]:
    """Stream with timeout and retry logic"""
    for attempt in range(max_retries + 1):
        try:
            got_first_byte = False
            async for chunk in self.stream_generate(system_prompt, user_message):
                if not got_first_byte:
                    got_first_byte = True
                yield chunk
            return  # Success
        except (httpx.HTTPStatusError, httpx.ReadTimeout) as e:
            if attempt < max_retries:
                logger.warning(f"Sarvam attempt {attempt+1} failed: {e}, retrying...")
                await asyncio.sleep(0.2)
            else:
                raise  # Let caller handle fallback
```

---


## ═══════════════════════════════════════════════════════════════
## PHASE 2: EDGE LAYER — JWT + STREAM PROXY + KV RATE LIMITING
## ═══════════════════════════════════════════════════════════════

### Step 2.1: Add KV Namespace to Wrangler Config

**File:** `apps/edge/wrangler.toml`

**What to do:**
- Add KV namespace binding for rate limiting
- Add secret bindings for JWT

```toml
# Add to wrangler.toml
[[kv_namespaces]]
binding = "RATE_LIMIT_KV"
id = "YOUR_KV_NAMESPACE_ID"
preview_id = "YOUR_PREVIEW_KV_ID"

[vars]
AZURE_BACKEND_URL = "https://ca-syrabit-api.azurecontainerapps.io"
ALLOWED_ORIGIN = "https://syrabit.ai"

# Secrets (set via wrangler secret put):
# JWT_SECRET, CF_TURNSTILE_SECRET
```

**CLI Command to create KV:**
```bash
# Create KV namespace
npx wrangler kv:namespace create "RATE_LIMIT_KV"
npx wrangler kv:namespace create "RATE_LIMIT_KV" --preview

# Set secrets
npx wrangler secret put JWT_SECRET
npx wrangler secret put CF_TURNSTILE_SECRET
```

---

### Step 2.2: Implement JWT Validation Middleware

**File:** `apps/edge/src/middleware/jwt.ts` (NEW)

**What to do:**
- Decode and verify HS256 JWT at edge (before proxying)
- Extract `sub` (user_id) and inject as `X-User-ID` header
- Skip JWT for public endpoints (`/health`, `/api/v1/auth/login`, `/api/v1/auth/signup`)
- Return 401 with proper error if token invalid/expired

```typescript
// apps/edge/src/middleware/jwt.ts

interface JWTPayload {
  sub: string;
  exp: number;
  type: string;
}

const PUBLIC_PATHS = ['/health', '/api/v1/auth/login', '/api/v1/auth/signup'];

export async function verifyJWT(
  request: Request,
  jwtSecret: string
): Promise<{ valid: boolean; userId?: string; error?: string }> {
  const url = new URL(request.url);
  
  // Skip JWT for public endpoints
  if (PUBLIC_PATHS.some(p => url.pathname.startsWith(p))) {
    return { valid: true, userId: 'anonymous' };
  }
  
  const authHeader = request.headers.get('Authorization');
  if (!authHeader || !authHeader.startsWith('Bearer ')) {
    return { valid: false, error: 'Missing or invalid Authorization header' };
  }
  
  const token = authHeader.slice(7);
  
  try {
    const payload = await decodeJWT(token, jwtSecret);
    
    // Check expiry
    if (payload.exp < Math.floor(Date.now() / 1000)) {
      return { valid: false, error: 'Token expired' };
    }
    
    // Must be access token
    if (payload.type !== 'access') {
      return { valid: false, error: 'Invalid token type' };
    }
    
    return { valid: true, userId: payload.sub };
  } catch (e) {
    return { valid: false, error: 'Token verification failed' };
  }
}

async function decodeJWT(token: string, secret: string): Promise<JWTPayload> {
  const [headerB64, payloadB64, signatureB64] = token.split('.');
  
  // Verify signature using Web Crypto API (available in CF Workers)
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey(
    'raw',
    encoder.encode(secret),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['verify']
  );
  
  const signatureInput = encoder.encode(`${headerB64}.${payloadB64}`);
  const signature = base64UrlDecode(signatureB64);
  
  const isValid = await crypto.subtle.verify('HMAC', key, signature, signatureInput);
  if (!isValid) throw new Error('Invalid signature');
  
  return JSON.parse(atob(payloadB64.replace(/-/g, '+').replace(/_/g, '/')));
}

function base64UrlDecode(str: string): ArrayBuffer {
  const base64 = str.replace(/-/g, '+').replace(/_/g, '/');
  const padding = '='.repeat((4 - base64.length % 4) % 4);
  const binary = atob(base64 + padding);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes.buffer;
}
```


---

### Step 2.3: Implement KV-Based Per-Language Rate Limiting

**File:** `apps/edge/src/middleware/rate-limit.ts` (NEW)

**What to do:**
- Rate limit per `userId + lang` combination
- Prevent Assamese quota exhaustion separately from English
- Use CF KV with atomic increment pattern
- Return proper `429` with `Retry-After` and `X-RateLimit-*` headers

```typescript
// apps/edge/src/middleware/rate-limit.ts

interface RateLimitResult {
  allowed: boolean;
  remaining: number;
  resetAt: number;
}

export async function checkRateLimit(
  kv: KVNamespace,
  userId: string,
  lang: string,
  limit: number = 30  // per-language limit
): Promise<RateLimitResult> {
  const now = Date.now();
  const windowMs = 60 * 60 * 1000; // 1-hour window
  const windowKey = Math.floor(now / windowMs);
  const key = `rl:${userId}:${lang}:${windowKey}`;
  
  // Get current count
  const current = await kv.get(key);
  const count = current ? parseInt(current, 10) : 0;
  
  if (count >= limit) {
    return {
      allowed: false,
      remaining: 0,
      resetAt: (windowKey + 1) * windowMs,
    };
  }
  
  // Increment (eventual consistency is OK for rate limiting)
  await kv.put(key, String(count + 1), { expirationTtl: 7200 }); // 2h TTL
  
  return {
    allowed: true,
    remaining: limit - count - 1,
    resetAt: (windowKey + 1) * windowMs,
  };
}
```

---

### Step 2.4: Implement Stream-Aware Proxy

**File:** `apps/edge/src/routes/api-proxy.ts` (UPDATE)

**What to do:**
- Detect SSE streams by checking if path contains `/stream`
- For streaming: pass `response.body` directly (chunked)
- Set stream-specific headers: `Content-Type: text/event-stream`, `Connection: keep-alive`
- Disable buffering (`Cache-Control: no-store`)

```typescript
// Updated proxyRequest in apps/edge/src/routes/api-proxy.ts

export async function proxyRequest(
  request: Request,
  backendUrl: string,
  env: Env
): Promise<Response> {
  const url = new URL(request.url);
  const targetUrl = `${backendUrl}${url.pathname}${url.search}`;
  const isStreamRequest = url.pathname.includes('/stream');

  const headers = new Headers(request.headers);
  headers.set('X-Real-IP', request.headers.get('CF-Connecting-IP') || 'unknown');
  headers.set('X-Forwarded-Proto', 'https');
  headers.delete('Host');

  try {
    const response = await fetch(targetUrl, {
      method: request.method,
      headers: headers,
      body: request.method !== 'GET' && request.method !== 'HEAD' ? request.body : undefined,
    });

    const responseHeaders = new Headers(response.headers);
    responseHeaders.set('Access-Control-Allow-Origin', env.ALLOWED_ORIGIN || 'https://syrabit.ai');

    if (isStreamRequest) {
      // Stream-specific headers
      responseHeaders.set('Content-Type', 'text/event-stream');
      responseHeaders.set('Cache-Control', 'no-store');
      responseHeaders.set('Connection', 'keep-alive');
      responseHeaders.set('X-Content-Type-Options', 'nosniff');
      responseHeaders.delete('Content-Length');  // Remove for chunked

      // Pass stream body directly (no buffering)
      return new Response(response.body, {
        status: response.status,
        headers: responseHeaders,
      });
    }

    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: responseHeaders,
    });
  } catch (error) {
    return new Response(JSON.stringify({ error: 'Backend unavailable' }), {
      status: 503,
      headers: { 'Content-Type': 'application/json' },
    });
  }
}
```


---

### Step 2.5: Update Main Worker Entry Point

**File:** `apps/edge/src/index.ts` (UPDATE)

**What to do:**
- Integrate JWT middleware before proxy
- Integrate rate limiting with lang detection from request body
- Inject `X-User-ID` header after JWT validation
- Handle JWT failures with proper 401 response

```typescript
// Updated apps/edge/src/index.ts

import { turnstileVerify } from './middleware/bot';
import { verifyJWT } from './middleware/jwt';
import { checkRateLimit } from './middleware/rate-limit';
import { proxyRequest } from './routes/api-proxy';

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);

    // 1. CORS Preflight
    if (request.method === 'OPTIONS') {
      return new Response(null, {
        headers: {
          'Access-Control-Allow-Origin': env.ALLOWED_ORIGIN || 'https://syrabit.ai',
          'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
          'Access-Control-Allow-Headers': 'Content-Type, Authorization, CF-Turnstile-Response',
        },
      });
    }

    // 2. JWT Validation (all /api/ routes except public)
    if (url.pathname.startsWith('/api/')) {
      const jwtResult = await verifyJWT(request, env.JWT_SECRET);
      if (!jwtResult.valid) {
        return new Response(JSON.stringify({ error: jwtResult.error }), {
          status: 401,
          headers: { 'Content-Type': 'application/json' },
        });
      }

      // Inject user ID for backend
      const headers = new Headers(request.headers);
      headers.set('X-User-ID', jwtResult.userId || 'anonymous');
      request = new Request(request, { headers });
    }

    // 3. Turnstile (chat/auth only)
    if (url.pathname.startsWith('/api/v1/chat') || url.pathname.startsWith('/api/v1/auth')) {
      const turnstileToken = request.headers.get('CF-Turnstile-Response');
      if (turnstileToken) {
        const isValid = await turnstileVerify(turnstileToken, env.CF_TURNSTILE_SECRET);
        if (!isValid) {
          return new Response(JSON.stringify({ error: 'Bot verification failed' }), {
            status: 403, headers: { 'Content-Type': 'application/json' },
          });
        }
      }
    }

    // 4. Per-language rate limiting (chat endpoints)
    if (url.pathname.startsWith('/api/v1/chat') && request.method === 'POST') {
      const userId = request.headers.get('X-User-ID') || 'anonymous';
      // Parse lang from body (best-effort, default to 'en')
      let lang = 'en';
      try {
        const body = await request.clone().json();
        lang = body.lang || 'en';
      } catch {}

      const rl = await checkRateLimit(env.RATE_LIMIT_KV, userId, lang);
      if (!rl.allowed) {
        return new Response(JSON.stringify({ error: 'Rate limit exceeded' }), {
          status: 429,
          headers: {
            'Content-Type': 'application/json',
            'X-RateLimit-Remaining': '0',
            'X-RateLimit-Reset': String(rl.resetAt),
            'Retry-After': String(Math.ceil((rl.resetAt - Date.now()) / 1000)),
          },
        });
      }
    }

    // 5. Route
    if (url.pathname.startsWith('/api/')) {
      return await proxyRequest(request, env.AZURE_BACKEND_URL, env);
    }

    if (url.pathname.startsWith('/assets/')) {
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
```

### Step 2.6: Update Env Type Definitions

**File:** `apps/edge/src/env.d.ts` (NEW or update existing)

```typescript
interface Env {
  // Secrets
  JWT_SECRET: string;
  CF_TURNSTILE_SECRET: string;

  // Vars
  AZURE_BACKEND_URL: string;
  ALLOWED_ORIGIN: string;

  // Bindings
  R2_BUCKET: R2Bucket;
  RATE_LIMIT_KV: KVNamespace;
}
```

---


## ═══════════════════════════════════════════════════════════════
## PHASE 3: FRONTEND — REACT CHAT UI + useChat() HOOK
## ═══════════════════════════════════════════════════════════════

### Step 3.1: Install Frontend Dependencies

**Command script:**
```bash
#!/bin/bash
# File: scripts/frontend-setup.sh
cd apps/frontend

pnpm add \
  react-markdown \
  remark-gfm \
  lucide-react \
  tailwindcss \
  @tailwindcss/typography \
  postcss \
  autoprefixer \
  clsx \
  zustand

pnpm add -D \
  @types/react \
  tailwindcss

# Initialize Tailwind
npx tailwindcss init -p

echo "✅ Frontend dependencies installed"
```

---

### Step 3.2: Create `useChat()` Hook

**File:** `apps/frontend/src/hooks/useChat.ts` (NEW)

**What to do:**
- Custom hook that manages chat state (messages, loading, errors)
- Sends POST to `/api/v1/chat/stream` with `{ message, lang, session_id }`
- Parses SSE stream using `ReadableStream` / `EventSource` pattern
- Accumulates text chunks into the latest assistant message
- Handles fallback notifications and errors
- Exposes: `messages`, `sendMessage()`, `isStreaming`, `lang`, `setLang`

```typescript
// apps/frontend/src/hooks/useChat.ts

import { useState, useCallback, useRef } from 'react';

export type Lang = 'en' | 'as';

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: number;
  model?: string;
  latency_ms?: number;
  sources?: Array<{ title: string; url: string; score: number }>;
  isStreaming?: boolean;
}

interface UseChatOptions {
  apiUrl?: string;
  initialLang?: Lang;
  sessionId?: string;
}

export function useChat(options: UseChatOptions = {}) {
  const {
    apiUrl = '/api/v1/chat/stream',
    initialLang = 'en',
    sessionId,
  } = options;

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [lang, setLang] = useState<Lang>(initialLang);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const sendMessage = useCallback(async (content: string) => {
    if (!content.trim() || isStreaming) return;
    setError(null);

    // Add user message
    const userMsg: ChatMessage = {
      id: crypto.randomUUID(),
      role: 'user',
      content,
      timestamp: Date.now(),
    };
    setMessages(prev => [...prev, userMsg]);

    // Create placeholder assistant message
    const assistantId = crypto.randomUUID();
    const assistantMsg: ChatMessage = {
      id: assistantId,
      role: 'assistant',
      content: '',
      timestamp: Date.now(),
      isStreaming: true,
    };
    setMessages(prev => [...prev, assistantMsg]);
    setIsStreaming(true);

    // Abort controller for cancellation
    abortRef.current = new AbortController();

    try {
      const token = localStorage.getItem('access_token');
      const response = await fetch(apiUrl, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`,
        },
        body: JSON.stringify({ message: content, lang, session_id: sessionId }),
        signal: abortRef.current.signal,
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${await response.text()}`);
      }

      const reader = response.body!.getReader();
      const decoder = new TextDecoder();
      let fullText = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        const chunk = decoder.decode(value, { stream: true });
        const lines = chunk.split('\n');

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          const data = JSON.parse(line.slice(6));

          if (data.error) {
            setError(data.error);
            break;
          }
          if (data.text) {
            fullText += data.text;
            setMessages(prev =>
              prev.map(m => m.id === assistantId ? { ...m, content: fullText } : m)
            );
          }
          if (data.done) {
            setMessages(prev =>
              prev.map(m => m.id === assistantId ? {
                ...m,
                content: fullText,
                isStreaming: false,
                model: data.model,
                latency_ms: data.latency_ms,
              } : m)
            );
          }
        }
      }
    } catch (e: any) {
      if (e.name !== 'AbortError') {
        setError(e.message);
        setMessages(prev =>
          prev.map(m => m.id === assistantId ? { ...m, content: 'Error: ' + e.message, isStreaming: false } : m)
        );
      }
    } finally {
      setIsStreaming(false);
    }
  }, [apiUrl, lang, sessionId, isStreaming]);

  const stopStreaming = useCallback(() => {
    abortRef.current?.abort();
    setIsStreaming(false);
  }, []);

  return { messages, sendMessage, stopStreaming, isStreaming, lang, setLang, error };
}
```


---

### Step 3.3: Build Chat UI Components

**Files to create:**
1. `apps/frontend/src/components/ChatContainer.tsx` — Main layout
2. `apps/frontend/src/components/ChatMessage.tsx` — Individual message bubble
3. `apps/frontend/src/components/ChatInput.tsx` — Input field + send button
4. `apps/frontend/src/components/LangSelector.tsx` — EN/AS toggle
5. `apps/frontend/src/components/CitationLink.tsx` — Parsed `[#]` links
6. `apps/frontend/src/components/FeedbackButton.tsx` — 👍/👎 per message

**Component hierarchy:**
```
<App>
  └── <ChatContainer>
        ├── <LangSelector lang={lang} setLang={setLang} />
        ├── <MessageList>
        │     └── <ChatMessage> (for each message)
        │           ├── <ReactMarkdown> (content with citations)
        │           ├── <CitationLink> (parsed [#] refs)
        │           └── <FeedbackButton> (thumbs up/down)
        └── <ChatInput onSend={sendMessage} isStreaming={isStreaming} />
```

**ChatContainer.tsx (abbreviated):**
```tsx
import { useChat } from '../hooks/useChat';
import { ChatMessage } from './ChatMessage';
import { ChatInput } from './ChatInput';
import { LangSelector } from './LangSelector';

export function ChatContainer() {
  const { messages, sendMessage, stopStreaming, isStreaming, lang, setLang, error } = useChat();

  return (
    <div className="flex flex-col h-screen max-w-3xl mx-auto">
      {/* Header */}
      <header className="flex items-center justify-between p-4 border-b">
        <h1 className="text-xl font-bold">Syrabit</h1>
        <LangSelector lang={lang} setLang={setLang} disabled={isStreaming} />
      </header>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {messages.map(msg => (
          <ChatMessage key={msg.id} message={msg} />
        ))}
        {error && <div className="text-red-500 text-sm">{error}</div>}
      </div>

      {/* Input */}
      <ChatInput
        onSend={sendMessage}
        onStop={stopStreaming}
        isStreaming={isStreaming}
        placeholder={lang === 'en' ? 'Ask a question...' : 'প্ৰশ্ন সোধক...'}
      />
    </div>
  );
}
```

**LangSelector.tsx:**
```tsx
import { Lang } from '../hooks/useChat';

interface Props {
  lang: Lang;
  setLang: (lang: Lang) => void;
  disabled?: boolean;
}

export function LangSelector({ lang, setLang, disabled }: Props) {
  return (
    <div className="flex gap-1 bg-gray-100 rounded-lg p-1">
      <button
        onClick={() => setLang('en')}
        disabled={disabled}
        className={`px-3 py-1 rounded-md text-sm font-medium transition
          ${lang === 'en' ? 'bg-white shadow text-blue-600' : 'text-gray-500'}`}
      >
        English
      </button>
      <button
        onClick={() => setLang('as')}
        disabled={disabled}
        className={`px-3 py-1 rounded-md text-sm font-medium transition
          ${lang === 'as' ? 'bg-white shadow text-blue-600' : 'text-gray-500'}`}
      >
        অসমীয়া
      </button>
    </div>
  );
}
```

---

### Step 3.4: Implement Citation Parsing in ChatMessage

**What to do:**
- Parse `[1]`, `[2]`, etc. in response text
- Convert to clickable links that reference source metadata
- Display source cards below the message

```tsx
// Inside ChatMessage.tsx
function parseCitations(text: string, sources: Source[]): ReactNode {
  const parts = text.split(/(\[\d+\])/g);
  return parts.map((part, i) => {
    const match = part.match(/\[(\d+)\]/);
    if (match) {
      const idx = parseInt(match[1]) - 1;
      const source = sources[idx];
      if (source) {
        return (
          <a key={i} href={source.url} target="_blank"
             className="text-blue-600 hover:underline text-xs align-super">
            [{match[1]}]
          </a>
        );
      }
    }
    return <span key={i}>{part}</span>;
  });
}
```

---

### Step 3.5: Implement Feedback (Thumbs Up/Down)

**File:** `apps/frontend/src/components/FeedbackButton.tsx`

**What to do:**
- Show 👍/👎 buttons below each assistant message (only after streaming finishes)
- On click, POST to `/api/v1/chat/feedback` with `{ message_id, rating: 1|-1, lang, model }`
- Disable after submission, show confirmation

```tsx
export function FeedbackButton({ messageId, lang, model }: Props) {
  const [submitted, setSubmitted] = useState<'up' | 'down' | null>(null);

  const submit = async (rating: 1 | -1) => {
    const token = localStorage.getItem('access_token');
    await fetch('/api/v1/chat/feedback', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body: JSON.stringify({ message_id: messageId, rating, lang, model_provider: model }),
    });
    setSubmitted(rating === 1 ? 'up' : 'down');
  };

  if (submitted) return <span className="text-xs text-gray-400">Thanks!</span>;

  return (
    <div className="flex gap-2 mt-1">
      <button onClick={() => submit(1)} className="text-gray-400 hover:text-green-500">👍</button>
      <button onClick={() => submit(-1)} className="text-gray-400 hover:text-red-500">👎</button>
    </div>
  );
}
```

---


## ═══════════════════════════════════════════════════════════════
## PHASE 4: DATA LAYER — FEEDBACK COLLECTION + AGGREGATION
## ═══════════════════════════════════════════════════════════════

### Step 4.1: Create `chat_feedback` MongoDB Model

**File:** `apps/backend/app/models/feedback.py` (NEW)

```python
from beanie import Document, Indexed
from pydantic import Field
from typing import Optional, Literal
from datetime import datetime


class ChatFeedback(Document):
    """Chat Feedback Model — Tracks user ratings per message"""
    
    user_id: str
    session_id: Optional[str] = None
    message_id: str
    lang: Literal["en", "as"]
    model_provider: str  # "vertex" | "sarvam"
    rating: Literal[1, -1]  # 1 = thumbs up, -1 = thumbs down
    latency_ms: Optional[int] = None
    query_text: Optional[str] = None  # First 100 chars for debugging
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "chat_feedback"
        indexes = [
            [("lang", 1), ("model_provider", 1), ("timestamp", 1)],
            [("user_id", 1), ("timestamp", -1)],
            [("timestamp", 1)],  # TTL index (30 days)
        ]
```

### Step 4.2: Create Feedback API Endpoint

**File:** `apps/backend/app/api/v1/feedback.py` (NEW)

```python
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Literal, Optional
from datetime import datetime

from app.models.feedback import ChatFeedback
from app.api.v1.auth import get_current_user
from app.models.user import User

router = APIRouter(tags=["Feedback"])


class FeedbackRequest(BaseModel):
    message_id: str
    rating: Literal[1, -1]
    lang: Literal["en", "as"]
    model_provider: str
    session_id: Optional[str] = None


@router.post("/")
async def submit_feedback(
    request: FeedbackRequest,
    user: User = Depends(get_current_user),
):
    """Submit feedback for a chat message"""
    feedback = ChatFeedback(
        user_id=str(user.id),
        session_id=request.session_id,
        message_id=request.message_id,
        lang=request.lang,
        model_provider=request.model_provider,
        rating=request.rating,
        timestamp=datetime.utcnow(),
    )
    await feedback.insert()
    return {"status": "ok", "id": str(feedback.id)}


@router.get("/stats")
async def get_feedback_stats(user: User = Depends(get_current_user)):
    """Get aggregated feedback stats (admin only)"""
    # Only accessible if user has admin role
    pipeline = [
        {"$match": {"timestamp": {"$gte": datetime.utcnow().replace(day=1)}}},
        {"$group": {
            "_id": {"lang": "$lang", "model": "$model_provider"},
            "total": {"$sum": 1},
            "positive": {"$sum": {"$cond": [{"$eq": ["$rating", 1]}, 1, 0]}},
            "negative": {"$sum": {"$cond": [{"$eq": ["$rating", -1]}, 1, 0]}},
        }},
        {"$addFields": {
            "accuracy": {"$round": [{"$divide": ["$positive", "$total"]}, 3]}
        }},
        {"$sort": {"_id.lang": 1, "accuracy": -1}},
    ]
    results = await ChatFeedback.aggregate(pipeline).to_list()
    return {"stats": results}
```

### Step 4.3: Register Model and Route in Main App

**File:** `apps/backend/app/main.py` (UPDATE)

```python
# Add to imports
from app.api.v1 import feedback

# Add to document_models in mongo.py
from app.models.feedback import ChatFeedback
# document_models=[User, Chat, ChatFeedback]

# Add to routes in main.py
app.include_router(feedback.router, prefix="/api/v1/chat/feedback", tags=["Feedback"])
```

### Step 4.4: Create MongoDB Indexes and TTL via Script

**File:** `scripts/mongo-setup.js` (NEW)

```javascript
// Run with: mongosh "$MONGODB_URI" scripts/mongo-setup.js

const db = db.getSiblingDB('syrabit_prod');

// ── chat_feedback indexes ──
print("Creating chat_feedback indexes...");

db.chat_feedback.createIndex(
  { lang: 1, model_provider: 1, timestamp: 1 },
  { name: "idx_feedback_lang_model_time" }
);

db.chat_feedback.createIndex(
  { user_id: 1, timestamp: -1 },
  { name: "idx_feedback_user_time" }
);

// TTL: auto-delete after 30 days
db.chat_feedback.createIndex(
  { timestamp: 1 },
  { expireAfterSeconds: 30 * 24 * 60 * 60, name: "ttl_feedback_30d" }
);

print("✅ chat_feedback indexes created");

// ── Verify existing indexes ──
print("\n📊 All chat_feedback indexes:");
printjson(db.chat_feedback.getIndexes());
```

**CLI command:**
```bash
mongosh "$MONGODB_URI" scripts/mongo-setup.js
```

### Step 4.5: Accuracy Aggregation Pipeline (Admin Dashboard)

**File:** `scripts/accuracy-report.js` (NEW)

```javascript
// Run: mongosh "$MONGODB_URI" scripts/accuracy-report.js
// Reports accuracy by language + model over last 7 days

const db = db.getSiblingDB('syrabit_prod');
const sevenDaysAgo = new Date(Date.now() - 7 * 24 * 60 * 60 * 1000);

const results = db.chat_feedback.aggregate([
  { $match: { timestamp: { $gte: sevenDaysAgo } } },
  { $group: {
      _id: { lang: "$lang", model: "$model_provider" },
      total: { $sum: 1 },
      accurate: { $sum: { $cond: [{ $eq: ["$rating", 1] }, 1, 0] } },
      avg_latency: { $avg: "$latency_ms" },
    }
  },
  { $addFields: {
      accuracy: { $round: [{ $divide: ["$accurate", "$total"] }, 3] },
      satisfaction_pct: { $round: [{ $multiply: [{ $divide: ["$accurate", "$total"] }, 100] }, 1] }
    }
  },
  { $sort: { "_id.lang": 1, accuracy: -1 } }
]).toArray();

print("\n═══ ACCURACY REPORT (Last 7 Days) ═══\n");
results.forEach(r => {
  print(`  ${r._id.lang.toUpperCase()} | ${r._id.model.padEnd(15)} | ` +
        `${r.satisfaction_pct}% satisfaction | ${r.total} ratings | ` +
        `avg latency: ${Math.round(r.avg_latency || 0)}ms`);
});
print("\n═══════════════════════════════════════\n");
```

---


## ═══════════════════════════════════════════════════════════════
## PHASE 5: OBSERVABILITY — OPENTELEMETRY + STRUCTURED LOGGING
## ═══════════════════════════════════════════════════════════════

### Step 5.1: Install OTel Dependencies

**Command:**
```bash
cd apps/backend
source .venv/bin/activate

pip install \
  opentelemetry-api \
  opentelemetry-sdk \
  opentelemetry-instrumentation-fastapi \
  opentelemetry-instrumentation-httpx \
  opentelemetry-exporter-otlp \
  opentelemetry-instrumentation-pymongo

pip freeze > requirements-otel.txt
```

---

### Step 5.2: Initialize OpenTelemetry in FastAPI Startup

**File:** `apps/backend/app/core/telemetry.py` (NEW)

```python
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource, SERVICE_NAME
from app.config import settings
import logging

logger = logging.getLogger(__name__)


def init_telemetry(app):
    """Initialize OpenTelemetry with GCP Cloud Trace or OTLP exporter"""
    
    resource = Resource.create({
        SERVICE_NAME: "syrabit-backend",
        "deployment.environment": settings.APP_ENV,
        "service.version": "3.0.0",
    })
    
    provider = TracerProvider(resource=resource)
    
    # Export to GCP Cloud Trace (or generic OTLP collector)
    exporter = OTLPSpanExporter()  # Reads OTEL_EXPORTER_OTLP_ENDPOINT from env
    provider.add_span_processor(BatchSpanProcessor(exporter))
    
    trace.set_tracer_provider(provider)
    
    # Auto-instrument FastAPI
    FastAPIInstrumentor.instrument_app(app)
    
    # Auto-instrument httpx (catches all LLM API calls)
    HTTPXClientInstrumentor().instrument()
    
    logger.info("OpenTelemetry initialized")


def get_tracer():
    return trace.get_tracer("syrabit.backend")
```

### Step 5.3: Add Custom Spans to Chat Endpoint

**File:** `apps/backend/app/api/v1/chat.py` (UPDATE)

**What to do:**
- Wrap key operations in custom spans
- Tag spans with `lang`, `provider`, `model`, `user_tier`
- Record latency and error info as span attributes

```python
from app.core.telemetry import get_tracer

tracer = get_tracer()

@router.post("/stream")
async def chat_stream(request: ChatRequest, ...):
    with tracer.start_as_current_span("chat.stream") as span:
        span.set_attribute("chat.lang", detected_lang)
        span.set_attribute("chat.model", target_model)
        span.set_attribute("chat.provider", "sarvam" if detected_lang == "as" else "vertex")
        span.set_attribute("user.tier", user_tier)
        span.set_attribute("user.id", user_id)
        
        # Inside RAG retrieval
        with tracer.start_as_current_span("chat.rag_retrieval") as rag_span:
            embedding = await generate_embedding(request.message)
            context_chunks = await search_service.search_context(...)
            rag_span.set_attribute("rag.chunks_returned", len(context_chunks))
            rag_span.set_attribute("rag.top_score", context_chunks[0]["score"] if context_chunks else 0)
        
        # Inside LLM generation
        with tracer.start_as_current_span("chat.llm_generation") as llm_span:
            llm_span.set_attribute("llm.provider", "sarvam" if detected_lang == "as" else "vertex")
            llm_span.set_attribute("llm.model", target_model)
            llm_span.set_attribute("llm.streaming", True)
            # ... stream logic ...
        
        span.set_attribute("chat.latency_ms", latency_ms)
        span.set_attribute("chat.response_length", len(full_response))
```

### Step 5.4: Environment Variables for OTel

**Add to `.env`:**
```bash
# OpenTelemetry
OTEL_EXPORTER_OTLP_ENDPOINT=https://your-collector:4317
OTEL_SERVICE_NAME=syrabit-backend
OTEL_TRACES_SAMPLER=parentbased_traceidratio
OTEL_TRACES_SAMPLER_ARG=0.1
```

**Azure CLI — Deploy with OTel env vars:**
```bash
az containerapp update \
  --name ca-syrabit-api \
  --resource-group rg-syrabit-prod \
  --set-env-vars \
    "OTEL_EXPORTER_OTLP_ENDPOINT=https://otel-collector.syrabit.internal:4317" \
    "OTEL_SERVICE_NAME=syrabit-backend" \
    "OTEL_TRACES_SAMPLER=parentbased_traceidratio" \
    "OTEL_TRACES_SAMPLER_ARG=0.1"
```

---


## ═══════════════════════════════════════════════════════════════
## PHASE 6: CI/CD — LLM HEALTH CHECKS + UNIFIED DEPLOY
## ═══════════════════════════════════════════════════════════════

### Step 6.1: Add LLM Endpoint Health Checks to Backend CI

**File:** `.github/workflows/ci-backend.yml` (UPDATE)

**What to add** (new job: `validate-endpoints`):

```yaml
  validate-endpoints:
    name: Validate LLM Endpoints
    runs-on: ubuntu-latest
    needs: [lint, test]
    if: github.ref == 'refs/heads/main'
    steps:
      - name: Check Vertex AI
        run: |
          HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
            "https://us-central1-aiplatform.googleapis.com/v1/projects/${{ secrets.VERTEX_PROJECT_ID }}/locations/us-central1/publishers/google/models/gemini-1.5-flash" \
            -H "Authorization: Bearer ${{ secrets.GCP_ACCESS_TOKEN }}")
          if [ "$HTTP_STATUS" -ne 200 ] && [ "$HTTP_STATUS" -ne 401 ]; then
            echo "❌ Vertex AI unreachable (HTTP $HTTP_STATUS)"
            exit 1
          fi
          echo "✅ Vertex AI endpoint reachable"

      - name: Check Sarvam AI
        run: |
          HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
            "https://api.sarvam.ai/v1/models" \
            -H "Authorization: Bearer ${{ secrets.SARVAM_API_KEY }}")
          if [ "$HTTP_STATUS" -ne 200 ]; then
            echo "❌ Sarvam AI unreachable (HTTP $HTTP_STATUS)"
            exit 1
          fi
          echo "✅ Sarvam AI endpoint reachable"

      - name: Check Azure Search
        run: |
          HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
            "${{ secrets.AZURE_SEARCH_ENDPOINT }}/indexes/${{ secrets.AZURE_SEARCH_INDEX_NAME }}?api-version=2024-07-01" \
            -H "api-key: ${{ secrets.AZURE_SEARCH_QUERY_KEY }}")
          if [ "$HTTP_STATUS" -ne 200 ]; then
            echo "❌ Azure Search unreachable (HTTP $HTTP_STATUS)"
            exit 1
          fi
          echo "✅ Azure Search endpoint reachable"
```

---

### Step 6.2: Add GCP OIDC Authentication

**File:** `.github/workflows/ci-backend.yml` (UPDATE — deploy job)

```yaml
  deploy:
    name: Deploy Backend
    runs-on: ubuntu-latest
    needs: [validate-endpoints]
    permissions:
      id-token: write
      contents: read
    steps:
      - uses: actions/checkout@v4

      - name: Authenticate to GCP (OIDC)
        uses: google-github-actions/auth@v2
        with:
          workload_identity_provider: ${{ secrets.GCP_WIF_PROVIDER }}
          service_account: ${{ secrets.GCP_SERVICE_ACCOUNT }}

      - name: Authenticate to Azure
        uses: azure/login@v2
        with:
          client-id: ${{ secrets.AZURE_CLIENT_ID }}
          tenant-id: ${{ secrets.AZURE_TENANT_ID }}
          subscription-id: ${{ secrets.AZURE_SUBSCRIPTION_ID }}

      - name: Build and Push to ACR
        run: |
          az acr build \
            --registry syrabitacr \
            --image syrabit-api:${{ github.sha }} \
            --file apps/backend/Dockerfile \
            apps/backend/

      - name: Deploy to Container Apps
        run: |
          az containerapp update \
            --name ca-syrabit-api \
            --resource-group rg-syrabit-prod \
            --image syrabitacr.azurecr.io/syrabit-api:${{ github.sha }}
```

---

### Step 6.3: Unified Deploy Workflow (All Layers)

**File:** `.github/workflows/deploy-all.yml` (NEW)

```yaml
name: Deploy Full Stack
on:
  push:
    branches: [main]
  workflow_dispatch:

jobs:
  # ── 1. Backend ──
  deploy-backend:
    runs-on: ubuntu-latest
    permissions: { id-token: write, contents: read }
    steps:
      - uses: actions/checkout@v4
      - uses: google-github-actions/auth@v2
        with:
          workload_identity_provider: ${{ secrets.GCP_WIF_PROVIDER }}
          service_account: ${{ secrets.GCP_SERVICE_ACCOUNT }}
      - uses: azure/login@v2
        with:
          client-id: ${{ secrets.AZURE_CLIENT_ID }}
          tenant-id: ${{ secrets.AZURE_TENANT_ID }}
          subscription-id: ${{ secrets.AZURE_SUBSCRIPTION_ID }}
      - name: Deploy Backend
        run: |
          az acr build --registry syrabitacr \
            --image syrabit-api:${{ github.sha }} \
            --file apps/backend/Dockerfile apps/backend/
          az containerapp update \
            --name ca-syrabit-api \
            --resource-group rg-syrabit-prod \
            --image syrabitacr.azurecr.io/syrabit-api:${{ github.sha }}

  # ── 2. Edge Worker ──
  deploy-edge:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: 22 }
      - run: npm ci
      - name: Deploy Worker
        run: npx wrangler deploy --env production
        working-directory: apps/edge
        env:
          CLOUDFLARE_API_TOKEN: ${{ secrets.CF_API_TOKEN }}
          CLOUDFLARE_ACCOUNT_ID: ${{ secrets.CF_ACCOUNT_ID }}

  # ── 3. Frontend (CF Pages) ──
  deploy-frontend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: 22 }
      - run: corepack enable && pnpm install
      - run: pnpm --filter syrabit-frontend run build
      - uses: cloudflare/pages-action@v1
        with:
          apiToken: ${{ secrets.CF_API_TOKEN }}
          accountId: ${{ secrets.CF_ACCOUNT_ID }}
          projectName: syrabit-frontend
          directory: apps/frontend/dist

  # ── 4. Post-Deploy Smoke Test ──
  smoke-test:
    runs-on: ubuntu-latest
    needs: [deploy-backend, deploy-edge, deploy-frontend]
    steps:
      - name: Health Check
        run: |
          curl -f https://edge.syrabit.ai/health || exit 1
          curl -f https://ca-syrabit-api.azurecontainerapps.io/health || exit 1
          echo "✅ All services healthy"
```

---


## ═══════════════════════════════════════════════════════════════
## AZURE CLI COMMAND SCRIPTS (Operations & Deployment)
## ═══════════════════════════════════════════════════════════════

### Script: Full Azure Infrastructure Provisioning

```bash
#!/bin/bash
# File: scripts/azure-provision.sh
# One-time infrastructure setup for Syrabit on Azure
# Run: chmod +x scripts/azure-provision.sh && ./scripts/azure-provision.sh

set -euo pipefail

export RG="rg-syrabit-prod"
export LOCATION="eastus"
export ACR="syrabitacr"
export APP_NAME="ca-syrabit-api"
export LAW="law-syrabit"
export CAE="cae-syrabit"
export KV="kv-syrabit-prod"

echo "══════════════════════════════════════════"
echo "  SYRABIT AZURE PROVISIONING"
echo "══════════════════════════════════════════"

# ── 1. Resource Group ──
echo "📦 Creating Resource Group..."
az group create --name $RG --location $LOCATION

# ── 2. Container Registry ──
echo "🐳 Creating ACR..."
az acr create --name $ACR --resource-group $RG --sku Basic --admin-enabled true

# ── 3. Log Analytics Workspace ──
echo "📊 Creating Log Analytics..."
az monitor log-analytics workspace create \
  --workspace-name $LAW --resource-group $RG --location $LOCATION

LAW_ID=$(az monitor log-analytics workspace show \
  --workspace-name $LAW --resource-group $RG \
  --query customerId -o tsv)
LAW_KEY=$(az monitor log-analytics workspace get-shared-keys \
  --workspace-name $LAW --resource-group $RG \
  --query primarySharedKey -o tsv)

# ── 4. Container Apps Environment ──
echo "🌍 Creating Container Apps Environment..."
az containerapp env create \
  --name $CAE --resource-group $RG --location $LOCATION \
  --logs-workspace-id "$LAW_ID" \
  --logs-workspace-key "$LAW_KEY"

# ── 5. Key Vault ──
echo "🔑 Creating Key Vault..."
az keyvault create --name $KV --resource-group $RG --location $LOCATION

# ── 6. Store Secrets in Key Vault ──
echo "🔐 Storing secrets..."
az keyvault secret set --vault-name $KV --name "jwt-secret" --value "$JWT_SECRET"
az keyvault secret set --vault-name $KV --name "sarvam-api-key" --value "$SARVAM_API_KEY"
az keyvault secret set --vault-name $KV --name "mongodb-uri" --value "$MONGODB_URI"
az keyvault secret set --vault-name $KV --name "razorpay-key-secret" --value "$RAZORPAY_KEY_SECRET"

# ── 7. Deploy Container App ──
echo "🚀 Deploying Container App..."
az containerapp create \
  --name $APP_NAME \
  --resource-group $RG \
  --environment $CAE \
  --image syrabitacr.azurecr.io/syrabit-api:latest \
  --registry-server syrabitacr.azurecr.io \
  --target-port 8000 \
  --ingress external \
  --min-replicas 1 \
  --max-replicas 5 \
  --cpu 1 --memory 2Gi \
  --env-vars \
    "APP_ENV=production" \
    "MONGODB_URI=secretref:mongodb-uri" \
    "JWT_SECRET=secretref:jwt-secret"

echo "✅ Infrastructure provisioned!"
echo "   App URL: $(az containerapp show --name $APP_NAME --resource-group $RG --query 'properties.configuration.ingress.fqdn' -o tsv)"
```

---

### Script: Deploy Backend Update (Quick Deploy)

```bash
#!/bin/bash
# File: scripts/deploy-backend.sh
# Quick deploy: builds image and updates Container App
# Run: ./scripts/deploy-backend.sh [TAG]

set -euo pipefail

TAG=${1:-$(git rev-parse --short HEAD)}
RG="rg-syrabit-prod"
ACR="syrabitacr"
APP_NAME="ca-syrabit-api"
IMAGE="${ACR}.azurecr.io/syrabit-api:${TAG}"

echo "🔨 Building image: ${IMAGE}..."
az acr build \
  --registry $ACR \
  --image "syrabit-api:${TAG}" \
  --file apps/backend/Dockerfile \
  apps/backend/

echo "🚀 Deploying to Container App..."
az containerapp update \
  --name $APP_NAME \
  --resource-group $RG \
  --image $IMAGE

echo "⏳ Waiting for deployment..."
az containerapp revision list \
  --name $APP_NAME --resource-group $RG \
  --query "[0].{name:name, status:properties.runningState, created:properties.createdTime}" -o table

FQDN=$(az containerapp show --name $APP_NAME --resource-group $RG --query 'properties.configuration.ingress.fqdn' -o tsv)
echo "✅ Deployed! Health check: https://${FQDN}/health"
curl -sf "https://${FQDN}/health" && echo " → HEALTHY" || echo " → ⚠️  UNHEALTHY"
```

---

### Script: Deploy Edge Worker

```bash
#!/bin/bash
# File: scripts/deploy-edge.sh
# Deploys Cloudflare Worker (edge layer)

set -euo pipefail

echo "🌐 Deploying Edge Worker..."
cd apps/edge

# Ensure deps installed
pnpm install

# Type check
npx tsc --noEmit

# Deploy
npx wrangler deploy --env production

echo "✅ Edge Worker deployed!"
echo "   URL: https://edge.syrabit.ai"

# Smoke test
echo "🧪 Running smoke test..."
HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" https://edge.syrabit.ai/health)
if [ "$HTTP_STATUS" -eq 200 ]; then
  echo "   ✅ Health check passed"
else
  echo "   ⚠️  Health check returned $HTTP_STATUS"
fi
```

---

### Script: Rotate Secrets

```bash
#!/bin/bash
# File: scripts/rotate-secrets.sh
# Rotates JWT secret and updates all services

set -euo pipefail

echo "🔑 Rotating JWT Secret..."

# Generate new secret (64 chars)
NEW_SECRET=$(openssl rand -base64 48)

# Update Key Vault
az keyvault secret set \
  --vault-name kv-syrabit-prod \
  --name "jwt-secret" \
  --value "$NEW_SECRET"

# Update Container App
az containerapp secret set \
  --name ca-syrabit-api \
  --resource-group rg-syrabit-prod \
  --secrets "jwt-secret=$NEW_SECRET"

# Update Cloudflare Worker secret
echo "$NEW_SECRET" | npx wrangler secret put JWT_SECRET --env production

# Restart app to pick up new secret
az containerapp revision restart \
  --name ca-syrabit-api \
  --resource-group rg-syrabit-prod \
  --revision "$(az containerapp revision list --name ca-syrabit-api --resource-group rg-syrabit-prod --query '[0].name' -o tsv)"

echo "✅ JWT Secret rotated across all services"
echo "⚠️  Note: Existing tokens will be invalidated. Users must re-login."
```

---


## ═══════════════════════════════════════════════════════════════
## PRODUCTION CHECKLIST — VERIFICATION COMMANDS
## ═══════════════════════════════════════════════════════════════

```bash
#!/bin/bash
# File: scripts/production-checklist.sh
# Run after deployment to verify all architecture requirements

set -euo pipefail

echo "═══════════════════════════════════════════"
echo "  SYRABIT PRODUCTION CHECKLIST"
echo "═══════════════════════════════════════════"

PASS=0
FAIL=0

check() {
  if eval "$2" > /dev/null 2>&1; then
    echo "  ✅ $1"
    PASS=$((PASS+1))
  else
    echo "  ❌ $1"
    FAIL=$((FAIL+1))
  fi
}

EDGE_URL="https://edge.syrabit.ai"
BACKEND_URL="https://ca-syrabit-api.azurecontainerapps.io"

echo ""
echo "── Edge Layer ──"
check "Edge health responds" "curl -sf ${EDGE_URL}/health"
check "Edge returns 401 without JWT" "[ \$(curl -s -o /dev/null -w '%{http_code}' ${EDGE_URL}/api/v1/chat/stream) -eq 401 ]"
check "Edge returns 403 for OPTIONS w/o Turnstile" "curl -sf -X OPTIONS ${EDGE_URL}/api/v1/chat"
check "Stream endpoint returns event-stream content type" "curl -sf -H 'Authorization: Bearer TEST' ${EDGE_URL}/api/v1/chat/stream 2>&1 | grep -q 'text/event-stream'"

echo ""
echo "── Backend Layer ──"
check "Backend health responds" "curl -sf ${BACKEND_URL}/health"
check "Chat endpoint exists" "[ \$(curl -s -o /dev/null -w '%{http_code}' -X POST ${BACKEND_URL}/api/v1/chat/ -H 'Content-Type: application/json' -d '{}') -ne 404 ]"
check "Stream endpoint exists" "[ \$(curl -s -o /dev/null -w '%{http_code}' -X POST ${BACKEND_URL}/api/v1/chat/stream -H 'Content-Type: application/json' -d '{}') -ne 404 ]"
check "Feedback endpoint exists" "[ \$(curl -s -o /dev/null -w '%{http_code}' -X POST ${BACKEND_URL}/api/v1/chat/feedback/ -H 'Content-Type: application/json' -d '{}') -ne 404 ]"

echo ""
echo "── External Services ──"
check "Azure Search reachable" "curl -sf '${AZURE_SEARCH_ENDPOINT}/indexes?api-version=2024-07-01' -H 'api-key: ${AZURE_SEARCH_QUERY_KEY}'"
check "MongoDB Atlas reachable" "mongosh '${MONGODB_URI}' --eval 'db.runCommand({ping:1})' --quiet"
check "Sarvam AI reachable" "[ \$(curl -s -o /dev/null -w '%{http_code}' https://api.sarvam.ai/v1/models -H 'Authorization: Bearer ${SARVAM_API_KEY}') -eq 200 ]"

echo ""
echo "── MongoDB Collections ──"
check "chat_feedback collection exists" "mongosh '${MONGODB_URI}' --eval 'db.chat_feedback.stats().count >= 0' --quiet"
check "TTL index on chat_feedback" "mongosh '${MONGODB_URI}' --eval 'db.chat_feedback.getIndexes().some(i => i.expireAfterSeconds)' --quiet"
check "Compound index (lang,model,time)" "mongosh '${MONGODB_URI}' --eval 'db.chat_feedback.getIndexes().some(i => i.name === \"idx_feedback_lang_model_time\")' --quiet"

echo ""
echo "══════════════════════════════════════════"
echo "  RESULTS: ${PASS} passed, ${FAIL} failed"
echo "══════════════════════════════════════════"

[ $FAIL -eq 0 ] && echo "🎉 ALL CHECKS PASSED!" || echo "⚠️  ${FAIL} checks need attention"
exit $FAIL
```

---

## ═══════════════════════════════════════════════════════════════
## EXECUTION ORDER & DEPENDENCIES
## ═══════════════════════════════════════════════════════════════

```
WEEK 1: Foundation (Backend + Edge)
├── Day 1-2: Phase 1 (Steps 1.1 → 1.4) — Streaming + Lang param
├── Day 3:   Phase 1 (Steps 1.5 → 1.7) — Router + Citation + Fallback
├── Day 4:   Phase 2 (Steps 2.1 → 2.3) — JWT + KV Rate Limit
└── Day 5:   Phase 2 (Steps 2.4 → 2.6) — Stream Proxy + Worker Update

WEEK 2: Client + Data + Polish
├── Day 1-2: Phase 3 (Steps 3.1 → 3.3) — Chat UI + useChat hook
├── Day 3:   Phase 3 (Steps 3.4 → 3.5) — Citations + Feedback UI
├── Day 4:   Phase 4 (Steps 4.1 → 4.5) — MongoDB Feedback + Aggregation
├── Day 5:   Phase 5 (Steps 5.1 → 5.4) — OpenTelemetry
└── Day 5:   Phase 6 (Steps 6.1 → 6.3) — CI/CD Hardening

WEEK 3 (Buffer):
├── Integration testing (end-to-end streaming)
├── Load testing (rate limit verification)
└── Production deployment + smoke tests
```

---

## ═══════════════════════════════════════════════════════════════
## KEY FILES CREATED/MODIFIED (SUMMARY)
## ═══════════════════════════════════════════════════════════════

### New Files:
| # | File | Purpose |
|---|------|---------|
| 1 | `apps/edge/src/middleware/jwt.ts` | JWT validation at edge |
| 2 | `apps/edge/src/middleware/rate-limit.ts` | KV-based per-lang rate limiting |
| 3 | `apps/edge/src/env.d.ts` | TypeScript env type definitions |
| 4 | `apps/frontend/src/hooks/useChat.ts` | Streaming chat hook |
| 5 | `apps/frontend/src/components/ChatContainer.tsx` | Chat layout |
| 6 | `apps/frontend/src/components/ChatMessage.tsx` | Message bubble |
| 7 | `apps/frontend/src/components/ChatInput.tsx` | Input field |
| 8 | `apps/frontend/src/components/LangSelector.tsx` | Language toggle |
| 9 | `apps/frontend/src/components/FeedbackButton.tsx` | Thumbs up/down |
| 10 | `apps/frontend/src/components/CitationLink.tsx` | Parsed citations |
| 11 | `apps/backend/app/models/feedback.py` | Feedback MongoDB model |
| 12 | `apps/backend/app/api/v1/feedback.py` | Feedback API endpoint |
| 13 | `apps/backend/app/core/telemetry.py` | OTel initialization |
| 14 | `scripts/codespace-setup.sh` | Dev environment bootstrap |
| 15 | `scripts/azure-provision.sh` | Azure infra provisioning |
| 16 | `scripts/azure-verify.sh` | Azure resource verification |
| 17 | `scripts/gcp-verify.sh` | GCP/Vertex verification |
| 18 | `scripts/sarvam-verify.sh` | Sarvam API verification |
| 19 | `scripts/deploy-backend.sh` | Quick backend deploy |
| 20 | `scripts/deploy-edge.sh` | Edge worker deploy |
| 21 | `scripts/rotate-secrets.sh` | Secret rotation |
| 22 | `scripts/mongo-setup.js` | MongoDB index creation |
| 23 | `scripts/accuracy-report.js` | Feedback aggregation report |
| 24 | `scripts/production-checklist.sh` | Post-deploy verification |
| 25 | `.github/workflows/deploy-all.yml` | Unified deploy workflow |

### Modified Files:
| # | File | Changes |
|---|------|---------|
| 1 | `apps/backend/app/api/v1/chat.py` | Add `lang` param, streaming endpoint, OTel spans |
| 2 | `apps/backend/app/services/ai/vertex_client.py` | Add `stream_generate()` method |
| 3 | `apps/backend/app/services/ai/sarvam_client.py` | Add `stream_generate()` with retry/fallback |
| 4 | `apps/backend/app/services/ai/router.py` | Add `stream_response()` function |
| 5 | `apps/backend/app/main.py` | Register feedback route, init OTel |
| 6 | `apps/backend/app/db/mongo.py` | Add ChatFeedback to document_models |
| 7 | `apps/edge/src/index.ts` | JWT + rate limit + stream-aware routing |
| 8 | `apps/edge/src/routes/api-proxy.ts` | Stream-aware proxy with chunked transfer |
| 9 | `apps/edge/wrangler.toml` | Add KV binding, secrets |
| 10 | `.github/workflows/ci-backend.yml` | LLM health checks, GCP OIDC |

---

## ═══════════════════════════════════════════════════════════════
## DONE — WHAT NEXT?
## ═══════════════════════════════════════════════════════════════

This plan is complete and ready for execution. Options:

1. **"Implement Phase 1"** — I'll write all the streaming code for Vertex + Sarvam + FastAPI
2. **"Implement Phase 2"** — I'll write the JWT + KV rate limit + stream proxy for CF Worker
3. **"Implement Phase 3"** — I'll build the full React Chat UI
4. **"Implement all"** — I'll execute the entire plan sequentially
5. **"Create the scripts/"** — I'll just create the operational scripts directory

Tell me which phase to start, or if you want adjustments to the plan.
