import { describe, expect, it } from 'vitest';

import { isValidEmailSyntax } from './email-validation';

describe('isValidEmailSyntax', () => {
  it.each([
    'student@example.com',
    'first.last+student@school.example.test',
    'student@localhost',
    'x!y@single-label',
  ])('accepts conventional address syntax: %s', (email) => {
    expect(isValidEmailSyntax(email)).toBe(true);
  });

  it.each([
    '',
    'missing-at.example.com',
    '@example.com',
    'student@',
    'student@@example.com',
    'first..last@example.com',
    '.first@example.com',
    'first.@example.com',
    'student@.example.com',
    'student@example..com',
    'student@-example.com',
    'student@example-.com',
    'student name@example.com',
    'student@example com',
  ])('rejects malformed address syntax: %s', (email) => {
    expect(isValidEmailSyntax(email)).toBe(false);
  });

  it('enforces local-part, domain-label, and total address length limits', () => {
    const maxLengthAddress = `${'a'.repeat(64)}@${'b'.repeat(63)}.${'c'.repeat(63)}.${'d'.repeat(61)}`;
    const tooLongAddress = `${'a'.repeat(64)}@${'b'.repeat(63)}.${'c'.repeat(63)}.${'d'.repeat(62)}`;

    expect(isValidEmailSyntax(maxLengthAddress)).toBe(true);
    expect(isValidEmailSyntax(tooLongAddress)).toBe(false);
    expect(isValidEmailSyntax(`${'a'.repeat(65)}@example.com`)).toBe(false);
    expect(isValidEmailSyntax(`student@${'a'.repeat(64)}.com`)).toBe(false);
  });
});