import { expect, test } from '@playwright/test';

test('通知连接显示进度与超时，后台刷新保留错误，迟到成功不自动登记', async ({ page }) => {
  await page.goto('./');
  await page.getByLabel('访问密码').fill('finevent-e2e-password-only');
  await page.getByRole('button', { name: '进入工作台' }).click();
  await page.getByRole('link', { name: '设置', exact: true }).click();
  await page.getByRole('link', { name: '设备、测试通知与诊断', exact: true }).click();
  await page.getByLabel('设备名称', { exact: true }).fill('连接超时验收设备');
  await page.clock.install();
  await page.evaluate(() => {
    // This test isolates subscription timeout handling, not native OS permission prompts.
    Object.defineProperty(Notification, 'requestPermission', {
      configurable: true,
      value: () => Promise.resolve('granted'),
    });
    let finish!: (value: null) => void;
    const operation = new Promise<null>((resolve) => {
      finish = resolve;
    });
    Object.defineProperty(navigator.serviceWorker, 'ready', {
      configurable: true,
      value: Promise.resolve({
        pushManager: {
          getSubscription: () => operation,
          subscribe: () => {
            throw new Error('迟到结果不应登记');
          },
        },
      }),
    });
    Object.assign(window, { finishNewsConnectionTest: finish });
  });
  let registrations = 0;
  await page.route('**/api/v1/push/subscriptions', (route) => {
    registrations++;
    return route.fulfill({ status: 500, json: { error: '不应发起登记' } });
  });
  await page.getByRole('button', { name: '启用这台设备的通知' }).click();
  await expect(page.getByRole('status')).toContainText('正在连接浏览器推送服务');
  await page.clock.fastForward(45000);
  await expect(page.getByRole('alert')).toContainText('连接浏览器推送服务超时');
  await expect(page.getByRole('button', { name: '启用这台设备的通知' })).toBeEnabled();
  await page.clock.fastForward(30000);
  await expect(page.getByRole('alert')).toContainText('连接浏览器推送服务超时');
  await page.evaluate(() =>
    (
      window as unknown as { finishNewsConnectionTest: (value: null) => void }
    ).finishNewsConnectionTest(null),
  );
  await expect(page.getByRole('alert')).toContainText('连接浏览器推送服务超时');
  expect(registrations).toBe(0);
});
