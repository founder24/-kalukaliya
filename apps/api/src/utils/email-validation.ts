const MAX_EMAIL_LENGTH = 254;
const MAX_LOCAL_PART_LENGTH = 64;
const MAX_DOMAIN_LENGTH = 253;

const LOCAL_PART_ATOM = /^[A-Za-z0-9!#$%&'*+\/=?^_`{|}~-]+$/;
const DOMAIN_LABEL = /^[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?$/;

/**
 * Validate conventional dot-atom email syntax without checking deliverability
 * or restricting which domains may sign up. Single-label domains are allowed.
 */
export function isValidEmailSyntax(email: string): boolean {
  if (email.length === 0 || email.length > MAX_EMAIL_LENGTH) return false;

  const at = email.indexOf('@');
  if (at <= 0 || at !== email.lastIndexOf('@') || at === email.length - 1) {
    return false;
  }

  const localPart = email.slice(0, at);
  const domain = email.slice(at + 1);
  if (
    localPart.length > MAX_LOCAL_PART_LENGTH
    || domain.length > MAX_DOMAIN_LENGTH
    || !localPart.split('.').every((atom) => atom.length > 0 && LOCAL_PART_ATOM.test(atom))
  ) {
    return false;
  }

  return domain.split('.').every((label) =>
    label.length > 0
    && label.length <= 63
    && DOMAIN_LABEL.test(label),
  );
}