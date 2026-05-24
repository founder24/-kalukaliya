import { useEffect } from 'react';
import {
  hydrateAdsOptOutFromServer,
  setAdsUserPlan,
  setAdsAuthChecked,
} from '@/utils/adsConfig';

export function useAdsSync(user, authChecked) {
  // Mirror user plan to ads module
  useEffect(() => {
    setAdsUserPlan(user?.plan ?? null);
  }, [user?.plan]);

  // Mark auth as checked for ad gate
  useEffect(() => {
    if (authChecked) {
      setAdsAuthChecked(true);
    }
  }, [authChecked]);
}

export function hydrateAdsFromUser(userData) {
  if (userData) {
    hydrateAdsOptOutFromServer(userData.ads_opt_out);
    setAdsUserPlan(userData.plan ?? null);
  } else {
    setAdsUserPlan(null);
  }
}
