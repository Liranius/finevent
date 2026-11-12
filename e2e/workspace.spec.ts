import { expect, test } from '@playwright/test';

import { startOfflineServer } from './offline-server.js';

test('真实 API：登录失败、键盘登录、三个验证页与退出', async ({ page }, testInfo) => {
  await page.goto('./');
  await page.getByLabel('访问密码').fill('wrong');
  await page.getByRole('button', { name: '进入工作台' }).click();
  await expect(page.getByRole('alert')).toHaveText('密码不正确');
  await page.getByLabel('访问密码').fill('finevent-e2e-password-only');
  await page.getByLabel('访问密码').press('Tab');
  await expect(page.getByRole('button', { name: '进入工作台' })).toBeFocused();
  await page.keyboard.press('Enter');
  await page.getByRole('link', { name: '设置', exact: true }).click();
  await page.getByRole('link', { name: '设备、测试通知与诊断', exact: true }).click();
  await expect(page.getByRole('heading', { name: '开发者功能', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '发送测试提醒' })).toBeDisabled();
  await page.getByRole('button', { name: '02 采集时效' }).click();
  await expect(page.getByText('当前服务未开启来源采样', { exact: false })).toBeVisible();
  await page.keyboard.press('Tab');
  await expect(page.getByRole('button', { name: '03 资源共存' })).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.getByText('AI 并发验证以服务器验收报告为准', { exact: false })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('desktop-resources.png'), fullPage: true });
  await page.getByRole('button', { name: '退出', exact: true }).click();
  await expect(page.getByLabel('访问密码')).toBeVisible();
  const response = await page.request.get('api/v1/status');
  expect(response.status()).toBe(401);
});

test.describe('真实离线资源', () => {
  test.use({ serviceWorkers: 'allow' });

  test('窄屏：无横向溢出、切换场景及离线应用资源', async ({
    page,
    context,
    baseURL,
    browserName,
  }, testInfo) => {
    const server = await startOfflineServer(baseURL);
    try {
      await page.setViewportSize({ width: 393, height: 852 });
      await page.goto(server.url);
      await page.getByLabel('访问密码').fill('finevent-e2e-password-only');
      await page.getByRole('button', { name: '进入工作台' }).click();
      await page.getByRole('link', { name: '设置', exact: true }).click();
      await page.getByRole('link', { name: '设备、测试通知与诊断', exact: true }).click();
      await expect(page.getByRole('heading', { name: '连接这台设备' })).toBeVisible();
      await page.getByLabel('测试场景').selectOption('reconnect');
      await expect(page.getByLabel('测试场景')).toHaveValue('reconnect');
      await page.getByLabel('发送延迟').selectOption('60');
      await expect(page.getByLabel('发送延迟')).toHaveValue('60');
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth > window.innerWidth,
      );
      expect(overflow).toBe(false);
      await page.screenshot({ path: testInfo.outputPath('mobile-push.png'), fullPage: true });
      await page.getByRole('button', { name: '03 资源共存' }).click();
      await expect(
        page.getByText('AI 并发验证以服务器验收报告为准', { exact: false }),
      ).toBeVisible();
      expect(
        await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth),
      ).toBe(false);
      await page.screenshot({ path: testInfo.outputPath('mobile-resources.png'), fullPage: true });
      await page.getByRole('button', { name: '01 通知送达' }).click();
      const workerScope = await page.evaluate(
        async () => (await navigator.serviceWorker.ready).scope,
      );
      expect(workerScope).toBe(`${new URL(page.url()).origin}/finevent/`);
      await expect
        .poll(() => page.evaluate(() => navigator.serviceWorker.controller !== null))
        .toBe(true);
      const cacheURLs = await page.evaluate(async () => {
        const result: string[] = [];
        for (const name of await caches.keys()) {
          const cache = await caches.open(name);
          result.push(...(await cache.keys()).map((request) => request.url));
        }
        return result;
      });
      expect(cacheURLs.some((url) => url.includes('/api/'))).toBe(false);
      await server.stop();
      await expect
        .poll(() =>
          fetch(`${server.url}healthz`).then(
            () => true,
            () => false,
          ),
        )
        .toBe(false);
      // TODO: Restore WebKit offline emulation after https://github.com/microsoft/playwright/issues/42775 is fixed in our pinned version.
      // Its offline flag rejects even local SW responses; stopping this owned origin keeps the cache assertions real.
      await context.setOffline(browserName !== 'webkit');
      await page.reload();
      await expect(page.getByRole('alert')).toBeVisible();
      await expect(page.getByLabel('访问密码')).toBeVisible();
      await context.setOffline(false);
    } finally {
      await server.stop();
    }
  });
});

test('通知记录：接受与接收分离，人工确认写入接口', async ({ page }) => {
  const now = Math.floor(Date.now() / 1000);
  await page.route('**/api/v1/status', (route) =>
    route.fulfill({
      json: {
        vapid_public_key: '',
        server_time: now,
        devices: [],
        resources: [],
        polls: [],
        coverage: [],
        news: [],
        latencies: [],
        collection_enabled: false,
        trendradar_enabled: false,
        deliveries: [
          {
            id: 7,
            label: '测试设备',
            scenario: 'locked',
            status: 'accepted',
            due: now,
            accepted_at: now,
            received_at: 0,
            clicked_at: 0,
            error: '',
            manual_result: 'pending',
          },
        ],
      },
    }),
  );
  let recorded = '';
  await page.route('**/api/v1/push/results/7', async (route) => {
    recorded = (route.request().postDataJSON() as { result: string }).result;
    await route.fulfill({ json: { ok: true } });
  });
  await page.goto('./');
  await page.getByLabel('访问密码').fill('finevent-e2e-password-only');
  await page.getByRole('button', { name: '进入工作台' }).click();
  await page.getByRole('link', { name: '设置', exact: true }).click();
  await page.getByRole('link', { name: '设备、测试通知与诊断', exact: true }).click();
  await expect(page.getByText('服务已接受', { exact: true })).toBeVisible();
  await expect(page.getByText('设备接收 —', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '记录第 7 条未看到通知' }).click();
  await expect(page.getByRole('status')).toHaveText('实际观察结果已保存。');
  expect(recorded).toBe('not_seen');
});
