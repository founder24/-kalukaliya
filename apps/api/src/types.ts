export interface Env {
  // D1 Database
  DB: D1Database;

  // R2 Storage
  R2_BUCKET: R2Bucket;

  // KV Namespaces
  CONTENT_KV: KVNamespace;
  RATE_LIMIT_KV: KVNamespace;

  // Vectorize
  VECTORIZE: VectorizeIndex;

  // Workers AI
  AI: Ai;

  // Secrets (set via wrangler secret put)
  JWT_SECRET: string;
  ADMIN_JWT_SECRET: string;
  RESET_TOKEN_SECRET: string;
  EDGE_SHARED_SECRET: string;
  RAZORPAY_KEY_ID: string;
  RAZORPAY_KEY_SECRET: string;
  RAZORPAY_WEBHOOK_SECRET: string;
  RESEND_API_KEY: string;
  POSTHOG_API_KEY?: string;
  INDEXNOW_API_KEY?: string;
  INDEXNOW_INTERNAL_SECRET?: string;
  TRANSLATE_CRON_SECRET?: string;
  TRUSTPILOT_PROFILE_URL?: string;
  TRUSTPILOT_BUSINESS_UNIT_ID?: string;
  TRUSTPILOT_RATING_VALUE?: string;
  TRUSTPILOT_RATING_COUNT?: string;
  WEB_SEARCH_ENABLED?: string;

  // Cloud Run fallback (optional — when set, unimplemented Workers routes proxy there)
  BACKEND_URL?: string;

  // R2 public URL base (e.g. https://assets.syrabit.ai) — required for file upload routes
  R2_PUBLIC_URL?: string;

  // Vars
  ALLOWED_ORIGINS: string;
  APP_ENV: string;
}

export interface JwtPayload {
  sub: string;       // user id
  role: string;      // student | educator | staff | admin
  type: 'access' | 'refresh';
  iat: number;
  exp: number;
}

export interface AdminJwtPayload {
  sub: string;
  role: 'staff' | 'admin';
  type: 'admin' | 'admin_access';
  iat: number;
  exp: number;
}
