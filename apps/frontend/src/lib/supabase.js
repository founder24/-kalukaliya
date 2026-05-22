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
