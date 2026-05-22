import { useState, useEffect, useCallback, useRef } from 'react';
import axios from 'axios';
import { API_BASE } from '@/utils/api';

function urlBase64ToUint8Array(base64String) {
  const padding = '='.repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
  const raw = atob(base64);
  return Uint8Array.from([...raw].map((c) => c.charCodeAt(0)));
}

export function usePushNotifications({ serverPushEnabled } = {}) {
  const [permission, setPermission] = useState(
    typeof Notification !== 'undefined' ? Notification.permission : 'default'
  );
  const [subscribed, setSubscribed] = useState(false);
  const [loading, setLoading]       = useState(false);
  const [error, setError]           = useState(null);
  const syncingRef = useRef(false);

  const isSupported =
    typeof window !== 'undefined' &&
    'serviceWorker' in navigator &&
    'PushManager' in window &&
    typeof Notification !== 'undefined';

  useEffect(() => {
    if (!isSupported) return;
    navigator.serviceWorker.ready.then((reg) => {
      reg.pushManager.getSubscription().then((sub) => {
        setSubscribed(!!sub);
      });
    });
  }, [isSupported]);

  const subscribe = useCallback(async () => {
    if (!isSupported) {
      setError('Push notifications are not supported in this browser.');
      return false;
    }
    setLoading(true);
    setError(null);
    try {
      const perm = await Notification.requestPermission();
      setPermission(perm);
      if (perm !== 'granted') {
        setError('Push permission denied.');
        setLoading(false);
        return false;
      }

      const { data } = await axios.get(`${API_BASE}/push/vapid-public-key`);
      const applicationServerKey = urlBase64ToUint8Array(data.public_key);

      const reg = await navigator.serviceWorker.ready;
      const sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey,
      });

      await axios.post(
        `${API_BASE}/push/subscribe`,
        { subscription: sub.toJSON() },
        { withCredentials: true }
      );

      setSubscribed(true);
      setLoading(false);
      return true;
    } catch (e) {
      setError(e?.response?.data?.detail || e?.message || 'Subscription failed.');
      setLoading(false);
      return false;
    }
  }, [isSupported]);

  const unsubscribe = useCallback(async () => {
    if (!isSupported) return false;
    setLoading(true);
    try {
      const reg = await navigator.serviceWorker.ready;
      const sub = await reg.pushManager.getSubscription();
      if (sub) {
        const endpoint = sub.endpoint;
        await sub.unsubscribe();
        await axios.delete(
          `${API_BASE}/push/subscribe`,
          { data: { endpoint }, withCredentials: true }
        );
      }
      setSubscribed(false);
    } catch (e) {
      setError(e?.message || 'Unsubscribe failed.');
    } finally {
      setLoading(false);
    }
  }, [isSupported]);

  useEffect(() => {
    if (!isSupported || serverPushEnabled === undefined || serverPushEnabled === null) return;
    if (syncingRef.current) return;

    if (serverPushEnabled && !subscribed && Notification.permission === 'granted') {
      syncingRef.current = true;
      subscribe().finally(() => { syncingRef.current = false; });
    }
  }, [isSupported, serverPushEnabled, subscribed, subscribe]);

  return { isSupported, permission, subscribed, loading, error, subscribe, unsubscribe };
}
