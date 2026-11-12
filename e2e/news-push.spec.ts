import { createECDH, randomUUID } from 'node:crypto';

import { expect, test, type Page } from '@playwright/test';

const endpoint = '/finevent/api/v1/news-push';
async function setup(page: Page) {
  await page.goto('./');
  await page.getByLabel('访问密码').fill('finevent-e2e-password-only');
  await page.getByRole('button', { name: '进入工作台' }).click();
  await expect(page.getByRole('heading', { name: '财经日历', exact: true })).toBeVisible();
  const ec = createECDH('prime256v1');
  ec.generateKeys();
  const response = await page.request.post('/finevent/api/v1/push/subscriptions', {
    headers: { Origin: new URL(page.url()).origin },
    data: {
      label: '新闻验收设备',
      subscription: {
        endpoint: `https://fcm.googleapis.com/fcm/send/local-news-${randomUUID()}`,
        keys: {
          p256dh: ec.getPublicKey().toString('base64url'),
          auth: Buffer.alloc(16).toString('base64url'),
        },
      },
    },
  });
  expect(response.ok()).toBe(true);
  await page.request.put(`${endpoint}/settings`, {
    headers: { Origin: new URL(page.url()).origin },
    data: { enabled: false, devices: [], categories: [], industries: [] },
  });
  await page.getByRole('button', { name: '新闻事件流', exact: true }).click();
  await page.getByRole('link', { name: '关注与推送', exact: true }).click();
  await expect(page.getByRole('heading', { name: '新闻关注与推送', exact: true })).toBeFocused();
  return (await response.json()) as { id: number };
}

test.afterEach(async ({ page }) => {
  await page.request.put(`${endpoint}/settings`, {
    headers: { Origin: new URL(page.url()).origin },
    data: { enabled: false, devices: [], categories: [], industries: [] },
  });
});

test('新闻关注：验证、保存、刷新保留草稿、键盘、窄屏与暂停', async ({ page }, testInfo) => {
  const device = await setup(page);
  await expect(page.getByText('尚无新闻通知。', { exact: false })).toBeVisible();
  await page.getByLabel('开启新闻推送', { exact: true }).check();
  await page.getByRole('button', { name: '保存新闻推送设置' }).click();
  await expect(page.getByRole('alert')).toContainText('请选择接收设备和关注范围');
  await page.getByRole('button', { name: '刷新推送记录' }).click();
  await expect(page.getByRole('alert')).toContainText('请选择接收设备和关注范围');
  await expect(page.getByLabel('开启新闻推送', { exact: true })).toBeChecked();
  await page.locator(`input[type="checkbox"][value="${device.id}"]`).check();
  await page.getByLabel('货币与流动性', { exact: true }).check();
  await page.getByLabel('金融', { exact: true }).check();
  await page.getByRole('button', { name: '保存新闻推送设置' }).click();
  await expect(page.getByRole('status')).toContainText('新闻推送已开启');
  await expect(page.getByRole('complementary', { name: '新闻推送规则与状态' })).toContainText(
    '已保存 1 个类别、1 个行业',
  );
  await page.screenshot({ path: testInfo.outputPath('news-push-desktop.png'), fullPage: true });
  await page.reload();
  await expect(page.getByLabel('货币与流动性', { exact: true })).toBeChecked();
  await expect(page.getByLabel('金融', { exact: true })).toBeChecked();
  await page.setViewportSize({ width: 393, height: 852 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.getByLabel('金属与材料', { exact: true }).focus();
  await page.keyboard.press('Space');
  await expect(page.getByLabel('金属与材料', { exact: true })).toBeChecked();
  await page.keyboard.press('Tab');
  await expect(page.getByRole('button', { name: '保存新闻推送设置' })).toBeFocused();
  await page.screenshot({ path: testInfo.outputPath('news-push-mobile.png'), fullPage: true });
  await page.getByLabel('开启新闻推送', { exact: true }).uncheck();
  await page.getByRole('button', { name: '保存新闻推送设置' }).click();
  await expect(page.getByRole('status')).toContainText('新闻推送已暂停');
  await page.getByRole('button', { name: '连接或管理通知设备' }).click();
  await expect(page.getByRole('heading', { name: '验证工作台', exact: true })).toBeVisible();
});

test('新闻通知记录区分接受和回执，点击记录或通知哈希打开详情', async ({ page }) => {
  await setup(page);
  const now = Math.floor(Date.now() / 1000);
  await page.route(`**${endpoint}`, async (route) => {
    const response = await route.fetch();
    const data = await response.json();
    await route.fulfill({
      json: {
        ...data,
        history: [
          {
            id: 9,
            news_id: 321,
            version: 2,
            title: '测试：降准修订',
            body: '仅用于本地页面测试',
            reason: '重要修订',
            created: now,
            due: now,
            expires: now + 900,
            status: 'accepted',
            label: '新闻验收设备',
            accepted_at: now,
            received_at: 0,
            clicked_at: 0,
            error: '',
          },
        ],
      },
    });
  });
  await page.route('**/api/v1/news/321', (route) =>
    route.fulfill({
      json: {
        item: {
          id: 321,
          source: 'wallstreetcn',
          source_id: 'test',
          title: '测试：降准修订',
          content: '本地通知跳转测试原文',
          url: 'https://example.org/news/321',
          published_at: now,
          first_seen: now,
          last_seen: now,
          ingested_at: now,
          updated_at: now,
          source_observed_at: 0,
          version: 2,
          baseline: false,
          classification: null,
        },
        revisions: [],
        related: [],
        revision_more: false,
        relation_scope: '测试',
      },
    }),
  );
  await page.getByRole('button', { name: '刷新推送记录' }).click();
  const records = page.getByRole('region', { name: '新闻通知记录' });
  await expect(records.getByText('推送服务已接受', { exact: true })).toBeVisible();
  await expect(records.getByText('设备已回执', { exact: true })).toHaveCount(0);
  await records.getByRole('link', { name: '测试：降准修订' }).focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('#news-detail-heading')).toBeFocused();
  await expect(page.getByText('本地通知跳转测试原文', { exact: true })).toBeVisible();
  await page.getByRole('link', { name: '关注与推送', exact: true }).click();
  await page.goto('./#news=321');
  await expect(page.locator('#news-detail-heading')).toBeFocused();
  await expect(page.getByText('本地通知跳转测试原文', { exact: true })).toBeVisible();
});

test('新闻设置读取失败可重试，未保存关注不被后台刷新覆盖', async ({ page }) => {
  await setup(page);
  await page.route(`**${endpoint}`, (route) =>
    route.fulfill({ status: 503, json: { error: '测试：服务暂时不可用' } }),
  );
  await page.getByLabel('宏观经济', { exact: true }).check();
  await page.getByRole('button', { name: '刷新推送记录' }).click();
  await expect(page.getByRole('alert')).toContainText('测试：服务暂时不可用');
  await expect(page.getByLabel('宏观经济', { exact: true })).toBeChecked();
  await page.unroute(`**${endpoint}`);
  await page.getByRole('button', { name: '重试读取推送记录' }).click();
  await expect(page.getByRole('alert')).toHaveCount(0);
  await expect(page.getByLabel('宏观经济', { exact: true })).toBeChecked();
});
