/**
 * Turnstile Bot Verification Middleware
 * Verifies Cloudflare Turnstile tokens to prevent bot abuse
 */

export async function turnstileVerify(token: string, secret: string): Promise<boolean> {
  try {
    const response = await fetch('https://challenges.cloudflare.com/turnstile/v0/siteverify', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        secret: secret,
        response: token,
      }),
    });

    const data = (await response.json()) as { success?: boolean };
    return data.success === true;
  } catch (error) {
    console.error('Turnstile verification failed:', error);
    return false;
  }
}
