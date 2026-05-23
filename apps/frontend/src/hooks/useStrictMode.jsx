/**
 * useStrictMode — togglable parental safety layer.
 *
 * - Persists locally for instant reads (no flash on route change).
 * - Hydrates from server settings on mount (so a fresh device sees
 *   the guardian's choice when signed in).
 * - When ON, the educational browser hides external links outside
 *   the allowlist and forces server-side allowlist enforcement.
 */
import { useState, useEffect, useCallback } from 'react';
import { studyApi } from '@/utils/studyApi';
import { eduGetAllowlist } from '@/utils/api';

const KEY = 'syrabit_strict_mode';

let _allowlistPromise = null;
let _allowlistData = null;

function _fetchAllowlist() {
  if (_allowlistData) return Promise.resolve(_allowlistData);
  if (_allowlistPromise) return _allowlistPromise;
  _allowlistPromise = eduGetAllowlist()
    .then((res) => {
      const d = res?.data || {};
      _allowlistData = {
        domains: new Set((d.domains || []).map((x) => String(x).toLowerCase())),
        eduSuffixes: (d.edu_suffixes || []).map((x) => String(x).toLowerCase()),
      };
      return _allowlistData;
    })
    .catch(() => {
      _allowlistPromise = null;
      return { domains: new Set(), eduSuffixes: [] };
    });
  return _allowlistPromise;
}

function _hostAllowed(host, data) {
  if (!host || !data) return false;
  const h = host.toLowerCase().replace(/^www\./, '');
  if (data.domains.has(h)) return true;
  for (const d of data.domains) {
    if (h === d || h.endsWith('.' + d)) return true;
  }
  for (const sfx of data.eduSuffixes) {
    const s = sfx.startsWith('.') ? sfx : '.' + sfx;
    if (h.endsWith(s)) return true;
  }
  return false;
}

export function useEduAllowlist(enabled) {
  const [data, setData] = useState(_allowlistData);
  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    _fetchAllowlist().then((d) => { if (!cancelled) setData(d); });
    return () => { cancelled = true; };
  }, [enabled]);
  return useCallback((host) => _hostAllowed(host, data), [data]);
}

let _cache = null;
const _subs = new Set();
function _read() {
  if (_cache !== null) return _cache;
  try { _cache = localStorage.getItem(KEY) === '1'; } catch { _cache = false; }
  return _cache;
}
function _write(v) {
  _cache = !!v;
  try { localStorage.setItem(KEY, _cache ? '1' : '0'); } catch {}
  _subs.forEach(fn => { try { fn(_cache); } catch {} });
}

export function useStrictMode() {
  const [strict, setStrict] = useState(_read());
  const [loading, setLoading] = useState(false);
  const [guardianLocked, setGuardianLocked] = useState(false);

  useEffect(() => {
    const fn = (v) => setStrict(v);
    _subs.add(fn);
    return () => _subs.delete(fn);
  }, []);

  useEffect(() => {
    let cancelled = false;
    studyApi.getSettings()
      .then((s) => {
        if (cancelled) return;
        if (typeof s?.strict_mode === 'boolean') _write(s.strict_mode);
        setGuardianLocked(!!s?.guardian_locked);
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, []);

  const update = useCallback(async (next, pin = '') => {
    setLoading(true);
    try {
      await studyApi.setSettings({ strict_mode: !!next, pin });
      _write(!!next);
      return { ok: true };
    } catch (e) {
      return { ok: false, code: e.code || e.message };
    } finally {
      setLoading(false);
    }
  }, []);

  return { strict, setStrict: update, loading, guardianLocked };
}

function isStrictModeOn() { return _read(); }
