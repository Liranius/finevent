import { createECDH, randomUUID } from 'node:crypto';

import { expect, test, type Page } from '@playwright/test';

async function login(page: Page) {
  await page.goto('./#/calendar');
  await page.getByLabel('访问密码').fill('finevent-e2e-password-only');
  await page.getByRole('button', { name: '进入工作台' }).click();
  await expect(page.getByRole('heading', { name: '财经日历', exact: true })).toBeVisible();
}
async function seed(page: Page) {
  const ec = createECDH('prime256v1');
  ec.generateKeys();
  const device = await page.request.post('api/v1/push/subscriptions', {
    headers: { Origin: new URL(page.url()).origin },
    data: {
      label: '本地提醒验收设备',
      subscription: {
        endpoint: `https://fcm.googleapis.com/fcm/send/local-${randomUUID()}`,
        keys: {
          p256dh: ec.getPublicKey().toString('base64url'),
          auth: Buffer.alloc(16).toString('base64url'),
        },
      },
    },
  });
  expect(device.status()).toBe(200);
  const start = new Date(Date.now() + 3 * 86400000).toISOString().replace('.000', '');
  const event = await page.request.post('api/v1/calendar', {
    headers: { Origin: new URL(page.url()).origin },
    data: {
      title: '自动提醒验收事件',
      start,
      end: start,
      timezone: 'UTC',
      precision: 'minute',
      status: 'confirmed',
      importance: 2,
      url: 'https://example.org/local-reminder-test',
      evidence: '仅用于独立本地测试库。',
    },
  });
  expect(event.status()).toBe(201);
  return {
    device: (await device.json()) as { id: number },
    event: (await event.json()) as { id: string; title: string },
  };
}

test.afterEach(async ({ page }) => {
  const result = await page.request.get('api/v1/reminders');
  if (result.ok()) {
    const data = await result.json();
    await page.request.put('api/v1/reminders/settings', {
      headers: { Origin: new URL(page.url()).origin },
      data: { ...data.settings, enabled: false },
    });
  }
});

test('日历提醒：单独关注、设置保存、重载、桌面与窄屏、暂停', async ({ page }, testInfo) => {
  await login(page);
  const { device, event } = await seed(page);
  await page.goto(`./#event=${event.id}`);
  await page.getByRole('button', { name: '关注此事件', exact: true }).click();
  await expect(page.getByText('已关注此事件。', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '提醒设置', exact: true }).first().click();
  await expect(page.getByRole('heading', { name: '提醒设置', exact: true })).toBeFocused();
  await page.getByLabel('开启日历自动提醒').check();
  await page.locator(`input[type="checkbox"][value="${device.id}"]`).check();
  await page.getByLabel('关注基金代码').fill('12');
  await page.getByRole('button', { name: '保存提醒设置' }).click();
  await expect(page.getByRole('alert')).toContainText('六位基金代码');
  await page.getByLabel('关注基金代码').fill('123456');
  await page.getByRole('button', { name: '保存提醒设置' }).click();
  await expect(page.getByRole('status')).toContainText('自动提醒已开启');
  await expect(page.locator('.reminder-agenda .reminder-event-link')).toHaveCount(2);
  await page.screenshot({ path: testInfo.outputPath('reminders-desktop.png'), fullPage: true });
  await page.reload();
  await expect(page.getByLabel('开启日历自动提醒')).toBeChecked();
  await expect(page.getByLabel('关注基金代码')).toHaveValue('123456');
  await page.setViewportSize({ width: 393, height: 852 });
  await expect(page.locator('.reminder-agenda .reminder-event-link')).toHaveCount(2);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
  await page.getByLabel('同时提醒基金开放最后一天').focus();
  await page.keyboard.press('Tab');
  await expect(page.getByRole('button', { name: '保存提醒设置' })).toBeFocused();
  await page.screenshot({ path: testInfo.outputPath('reminders-mobile.png'), fullPage: true });
  await page.getByLabel('开启日历自动提醒').uncheck();
  await page.getByRole('button', { name: '保存提醒设置' }).click();
  await expect(page.getByRole('status')).toContainText('自动提醒已暂停');
  await expect(page.locator('.reminder-agenda .reminder-event-link')).toHaveCount(0);
  await expect(page.getByRole('region', { name: '自动提醒记录' })).toHaveCount(0);
  await page.getByRole('link', { name: '返回设置', exact: true }).click();
  await page.getByRole('link', { name: '提醒记录', exact: true }).click();
  await expect(
    page.getByRole('region', { name: '自动提醒记录' }).getByText('已撤销', { exact: true }),
  ).toHaveCount(2);
  await page
    .getByRole('region', { name: '自动提醒记录' })
    .getByRole('button', { name: event.title, exact: true })
    .first()
    .click();
  await expect(page.getByRole('heading', { name: event.title, level: 1 })).toBeFocused();
  await page.getByRole('button', { name: '返回提醒记录', exact: true }).click();
  await expect(page.getByRole('heading', { name: '提醒记录', level: 1 })).toBeFocused();
});

test('改期和取消会撤销旧计划，通知链接定位事件详情', async ({ page }) => {
  await login(page);
  const { device, event } = await seed(page);
  const config = await page.request.get('api/v1/reminders');
  const { settings } = await config.json();
  expect(
    (
      await page.request.put('api/v1/reminders/settings', {
        headers: { Origin: new URL(page.url()).origin },
        data: { ...settings, enabled: true, devices: [device.id], kinds: [], fund_codes: [] },
      })
    ).ok(),
  ).toBe(true);
  await page.goto(`./#event=${event.id}`);
  await page.getByRole('button', { name: '关注此事件', exact: true }).click();
  await expect(
    page.getByRole('region', { name: '此事件的提醒' }).getByRole('listitem'),
  ).toHaveCount(2);
  await page.getByRole('link', { name: '设置', exact: true }).click();
  await page.getByRole('link', { name: '设备、测试通知与诊断', exact: true }).click();
  await expect(page.getByRole('heading', { name: '开发者功能', exact: true })).toBeVisible();
  await page.goto(`./#event=${event.id}`);
  await expect(page.getByRole('heading', { name: event.title, exact: true })).toBeVisible();
  await page.getByRole('button', { name: '修改日期、条件或状态' }).click();
  const changed = new Date(Date.now() + 5 * 86400000).toISOString().slice(0, 16);
  await page.getByLabel('开始时间').fill(changed);
  await page.getByLabel('结束时间').fill(changed);
  await page.getByRole('button', { name: '保存事件' }).click();
  await expect(page.getByText(/手工补录 · 已改期/)).toBeVisible();
  await expect
    .poll(
      async () => {
        const r = await page.request.get('api/v1/reminders');
        const d = await r.json();
        return d.history.filter(
          (x: { event_id: string; status: string }) =>
            x.event_id === event.id && x.status === 'cancelled',
        ).length;
      },
      { timeout: 15000 },
    )
    .toBe(2);
  await page.getByRole('button', { name: '修改日期、条件或状态' }).click();
  await page.getByLabel('确定程度').selectOption('cancelled');
  await page.getByRole('button', { name: '保存事件' }).click();
  await expect(page.getByText('事件已取消，尚未发送的提醒将撤销。')).toBeVisible();
  await expect
    .poll(
      async () => {
        const r = await page.request.get('api/v1/reminders');
        const d = await r.json();
        return d.upcoming.filter((x: { event_id: string }) => x.event_id === event.id).length;
      },
      { timeout: 15000 },
    )
    .toBe(0);
});

test('提醒设置加载失败可以重试，设备入口可以返回日历', async ({ page }) => {
  await login(page);
  await page.route('**/api/v1/reminders', (route) =>
    route.fulfill({ status: 503, json: { error: '提醒服务暂不可用' } }),
  );
  await page.getByRole('link', { name: '设置', exact: true }).click();
  await page.getByRole('link', { name: '日历提醒设置', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('提醒服务暂不可用');
  await page.unroute('**/api/v1/reminders');
  await page.getByRole('button', { name: '重试提醒设置' }).click();
  await page.getByRole('button', { name: '管理接收设备' }).click();
  await expect(page.getByRole('heading', { name: '开发者功能', exact: true })).toBeVisible();
  await page.getByRole('link', { name: '财经日历', exact: true }).click();
  await expect(page.getByRole('heading', { name: '财经日历', exact: true })).toBeVisible();
});
