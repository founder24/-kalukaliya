import { memo } from 'react';
import { useAuth } from '@/context/AuthContext';
import { BottomNav } from './BottomNav';
import { PublicBottomNav } from './PublicBottomNav';

export const MobileNavSwitch = memo(function MobileNavSwitch() {
  const { user } = useAuth();
  return user ? <BottomNav /> : <PublicBottomNav />;
});
