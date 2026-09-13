export const isStaffOrAdmin = (user) =>
  user?.role === 'staff' || user?.role === 'admin';

// The API treats null capabilities as legacy full access. An explicit array
// remains capability-scoped so limited staff accounts keep their existing
// content-operation restrictions.
export const canStaffCapability = (user, capability) =>
  isStaffOrAdmin(user) && (
    user.role === 'admin'
    || user.capabilities === null
    || (Array.isArray(user.capabilities) && user.capabilities.includes(capability))
  );