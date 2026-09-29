import { randomBytes, randomUUID } from 'node:crypto';
import { test, expect } from '@playwright/test';

const stagingWorkerUrl = process.env.STAGING_WORKER_URL?.replace(/\/+$/, '');
const stagingE2eEnabled = process.env.STAGING_E2E === '1' && Boolean(stagingWorkerUrl);

test.use({ trace: 'off' });

test.describe('Isolated staging browser gates', () => {
  test.skip(!stagingE2eEnabled, 'Set STAGING_E2E=1 and STAGING_WORKER_URL to run staging-only checks.');

  test('the public Worker gate blocks requests without its secret header', async ({ request }) => {
    const workerUrl = new URL(stagingWorkerUrl!);
    expect(workerUrl.protocol).toBe('https:');
    expect(workerUrl.hostname).toBe('syrabitworker-staging.axomxplain.workers.dev');
    expect(workerUrl.origin).toBe(stagingWorkerUrl);

    const response = await request.get(`${stagingWorkerUrl}/health`);
    expect(response.status()).toBe(401);
    await expect(response.json()).resolves.toMatchObject({
      error_code: 'staging_access_required',
    });
  });

  test('a staging student can save profile selectors; document markers render without chat generation', async ({ page }) => {
    page.on('pageerror', (error) => console.log(`[staging-e2e] page error: ${error.message}`));
    const email = `staging-e2e-${randomUUID()}@example.invalid`;
    const password = `Stage-${randomBytes(24).toString('base64url')}!`;
    const anonymousId = `anon_${randomBytes(16).toString('hex')}`;
    let cleanupToken: string | null = null;

    try {
      await page.goto('/login');
      const signup = await page.evaluate(async ({ email, password, anonymousId }) => {
        const response = await fetch('/api/v1/auth/signup', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'x-anon-id': anonymousId,
          },
          body: JSON.stringify({ email, password, name: 'Staging Gate Student' }),
        });
        return { status: response.status, body: await response.json() };
      }, { email, password, anonymousId });

      if (signup.status === 201 && typeof signup.body.access_token === 'string') {
        cleanupToken = signup.body.access_token;
      }
      expect(signup.status).toBe(201);
      expect(signup.body.user?.email).toBe(email);
      expect(typeof signup.body.access_token).toBe('string');

      await page.evaluate((token) => {
        sessionStorage.setItem('syrabit_token', token);
      }, signup.body.access_token);
      await page.goto('/onboarding');
      await expect(page.getByRole('heading', { name: 'Set Up Your Profile' })).toBeVisible();

      await page.getByTestId('board-option-staging-en-board').click();
      await page.getByTestId('onboarding-next-button').click();
      await page.getByTestId('class-option-staging-en-class').click();
      await page.getByTestId('onboarding-next-button').click();
      await page.getByTestId('stream-option-staging-en-stream').click();
      await page.getByTestId('onboarding-finish-button').click();
      await expect(page).toHaveURL(/\/library$/);

      const profileRequests = Promise.all([
        page.waitForResponse((response) => {
          const url = new URL(response.url());
          return response.request().method() === 'GET' && url.pathname === '/api/v1/user/profile';
        }),
        page.waitForResponse((response) => {
          const url = new URL(response.url());
          return response.request().method() === 'GET' && url.pathname === '/api/v1/user/stats';
        }),
      ]);
      await page.goto('/profile');
      const [profileResponse, statsResponse] = await profileRequests;
      expect(profileResponse.status()).toBe(200);
      expect(statsResponse.status()).toBe(200);
      expect((await profileResponse.json()).name).toBe('Staging Gate Student');
      await expect(page.getByTestId('profile-page')).toBeVisible();
      const nameField = page.getByTestId('edit-field-name');
      await expect(nameField).toContainText('Staging Gate Student');

      await page.getByRole('button', { name: /^Subject\b/ }).click();
      await page.getByRole('button', { name: 'STAGING TEST SUBJECT', exact: true }).click();

      const saveProfileRequest = page.waitForRequest((request) =>
        request.method() === 'PATCH' && new URL(request.url()).pathname === '/api/v1/user/profile',
      );
      const saveProfileResponse = page.waitForResponse((response) =>
        response.request().method() === 'PATCH'
        && new URL(response.url()).pathname === '/api/v1/user/profile',
      );
      await page.getByRole('button', { name: 'Save Academic Details', exact: true }).click();
      const [profileRequest, saveResponse] = await Promise.all([saveProfileRequest, saveProfileResponse]);
      expect(JSON.parse(profileRequest.postData() || '{}')).toMatchObject({
        board_id: 'staging-en-board',
        board_name: 'STAGING TEST BOARD',
        class_id: 'staging-en-class',
        class_name: 'STAGING TEST CLASS',
        stream_id: 'staging-en-stream',
        stream_name: 'STAGING TEST STREAM',
        selected_subjects: [{ id: 'staging-en-subject', name: 'STAGING TEST SUBJECT' }],
      });
      expect(saveResponse.status()).toBe(200);

      await page.reload();
      await expect(page.getByRole('button', { name: /^Board\b/ })).toContainText('STAGING TEST BOARD');
      await expect(page.getByRole('button', { name: /^Class \/ Semester\b/ })).toContainText('STAGING TEST CLASS');
      await expect(page.getByRole('button', { name: /^Stream\b/ })).toContainText('STAGING TEST STREAM');
      await expect(page.getByRole('button', { name: /^Subject\b/ })).toContainText('STAGING TEST SUBJECT');

      let chatGenerationRequests = 0;
      await page.route('**/api/v1/chat/stream', async (route) => {
        chatGenerationRequests += 1;
        await route.abort();
      });

      for (const marker of ['has_document=1', 'document_id=staging-document-fixture']) {
        await page.goto(`/chat?${marker}`);
        await expect(page.getByText(
          /Document loaded as primary source|ডকুমেণ্ট প্ৰাথমিক উৎস হিচাপে লোড হৈছে/,
        )).toBeVisible();
      }
      expect(chatGenerationRequests).toBe(0);
    } finally {
      if (cleanupToken) {
        const cleanupStatus = await page.evaluate(async (token) => {
          const response = await fetch('/api/v1/user/staging-e2e-account', {
            method: 'DELETE',
            headers: { Authorization: `Bearer ${token}` },
          });
          return response.status;
        }, cleanupToken);
        expect(cleanupStatus).toBe(200);
      }
    }
  });
});
