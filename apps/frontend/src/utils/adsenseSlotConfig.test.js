import { describe, expect, it } from 'vitest';
import {
  ADSENSE_PLACEMENT_ENV_KEYS,
  ADSENSE_SLOT_ENV_KEYS,
  validateAdsenseSlotEnv,
} from './adsenseSlotConfig';

function validEnvironment() {
  return Object.fromEntries(
    ADSENSE_SLOT_ENV_KEYS.map((key, index) => [key, String(100000 + index)]),
  );
}

describe('AdSense placement configuration', () => {
  it('declares one environment key for every manual placement', () => {
    expect(Object.keys(ADSENSE_PLACEMENT_ENV_KEYS)).toHaveLength(21);
    expect(new Set(ADSENSE_SLOT_ENV_KEYS).size).toBe(ADSENSE_SLOT_ENV_KEYS.length);
  });

  it('accepts complete unique numeric slot IDs', () => {
    expect(validateAdsenseSlotEnv(validEnvironment())).toMatchObject({
      valid: true,
      missing: [],
      invalid: [],
      duplicates: [],
    });
  });

  it('rejects missing, malformed, and duplicate IDs', () => {
    const environment = validEnvironment();
    environment[ADSENSE_SLOT_ENV_KEYS[0]] = '';
    environment[ADSENSE_SLOT_ENV_KEYS[1]] = 'not-a-slot';
    environment[ADSENSE_SLOT_ENV_KEYS[2]] = environment[ADSENSE_SLOT_ENV_KEYS[3]];

    expect(validateAdsenseSlotEnv(environment)).toMatchObject({
      valid: false,
      missing: [ADSENSE_SLOT_ENV_KEYS[0]],
      invalid: [ADSENSE_SLOT_ENV_KEYS[1]],
      duplicates: [`${ADSENSE_SLOT_ENV_KEYS[3]} duplicates ${ADSENSE_SLOT_ENV_KEYS[2]}`],
    });
  });
});