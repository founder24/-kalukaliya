/**
 * Supabase Client - Used ONLY for OAuth Social Login (Google).
 *
 * Auth Flow:
 * 1. User clicks "Sign in with Google"
 * 2. Supabase handles the OAuth redirect and returns a Supabase session
 * 3. Frontend extracts Supabase access token
 * 4. Frontend calls POST /api/v1/auth/google with the Supabase token
 * 5. Backend verifies the Supabase token, creates/finds the user in MongoDB
 * 6. Backend returns its own JWT (access_token + refresh_token)
 * 7. Frontend stores the backend JWT for all subsequent API calls
 *
 * The Supabase session is NOT used for API authentication - only for
 * initiating the OAuth flow. All API calls use the backend-issued JWT.
 */
import { createClient } from '@supabase/supabase-js';

const supabaseUrl = import.meta.env.VITE_SUPABASE_URL;
const supabaseAnonKey = import.meta.env.VITE_SUPABASE_ANON_KEY;

const hasCredentials = !!(supabaseUrl && supabaseAnonKey);

if (!hasCredentials) {
  console.warn('[supabase] VITE_SUPABASE_URL or VITE_SUPABASE_ANON_KEY not set — auth will not work');
}

// Guard: createClient throws "supabaseUrl is required." when passed an empty
// string, which crashes the entire module graph at load time (e.g. in CI
// where Supabase env vars are intentionally absent).  Export a null-safe
// no-op stub instead so React can still mount and non-auth features work.
export const supabase = hasCredentials
  ? createClient(supabaseUrl, supabaseAnonKey, {
      auth: {
        persistSession: true,
        autoRefreshToken: true,
        storageKey: 'syrabit_supabase_session',
      },
    })
  : null;
