import { expect, test, type Page, type Locator } from '@playwright/test';

import { readerDevices } from './reader-devices.js';

const destinations = [
  { group: '通知与提醒', link: '报道关注与推送', title: '报道关注与推送', path: '/news/push' },
  {
    group: '通知与提醒',
    link: '报道通知记录',
    title: '报道通知记录',
    path: '/settings/news-notifications',
  },
  { group: '通知与提醒', link: '日历提醒设置', title: '提醒设置', path: '/calendar/reminders' },
  { group: '通知与提醒', link: '提醒记录', title: '提醒记录', path: '/settings/reminder-history' },
  {
    group: '来源状态',
    link: '采集通道状态',
    title: '采集通道状态',
    path: '/settings/news-channels',
  },
  {
    group: '来源状态',
    link: '日历官方来源',
    title: '日历官方来源',
    path: '/settings/calendar-sources',
  },
  { group: 'AI 解读', link: 'AI 设置与预算', title: 'AI 设置与预算', path: '/ai' },
  { group: 'AI 解读', link: 'AI 解读记录', title: 'AI 解读记录', path: '/settings/ai-history' },
  {
    group: '开发者功能',
    link: '设备、测试通知与诊断',
    title: '开发者功能',
    path: '/verification/push',
  },
];

async function signIn(page: Page, path: string) {
  await page.goto('./#' + path);
  await page.getByLabel('访问密码').fill('finevent-e2e-password-only');
  await page.getByRole('button', { name: '进入工作台' }).click();
}

async function visitSettings(page: Page, activate: (target: Locator) => Promise<void>) {
  await signIn(page, '/news');
  const nav = page.getByRole('navigation', { name: '主导航' });
  const settings = nav.getByRole('link', { name: '设置', exact: true });
  await activate(settings);
  await expect(page.getByRole('heading', { name: '设置', exact: true })).toBeFocused();
  for (const { group, link, title, path } of destinations) {
    const entry = page
      .getByRole('region', { name: group, exact: true })
      .getByRole('link', { name: link, exact: true });
    // Leave the row in the visible reading area above the fixed mobile navigation.
    await entry.evaluate((el) => el.scrollIntoView({ block: 'nearest' }));
    expect(
      await entry.evaluate((el) => {
        const box = el.getBoundingClientRect();
        return el.contains(
          document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2),
        );
      }),
    ).toBe(true);
    const top = await entry.evaluate((el) => el.getBoundingClientRect().top);
    await activate(entry);
    await expect(page).toHaveURL((url) => url.hash === '#' + path);
    await expect(page.getByRole('heading', { name: title, level: 1, exact: true })).toBeFocused();
    await expect(settings).toHaveAttribute('aria-current', 'page');
    await activate(page.getByRole('link', { name: '返回设置', exact: true }));
    await expect(entry).toBeFocused();
    expect(
      Math.abs((await entry.evaluate((el) => el.getBoundingClientRect().top)) - top),
    ).toBeLessThanOrEqual(1);
  }
}

test.describe('桌面设置目录', () => {
  test.use({ viewport: { width: 1366, height: 900 } });

  test('键盘进入各组并返回原入口', async ({ page }) => {
    await visitSettings(page, (target) => target.press('Enter'));
    const group = page.getByRole('region', { name: '来源状态', exact: true });
    const label = await group.getByRole('heading').boundingBox();
    const entry = await group.getByRole('link').first().boundingBox();
    expect(label!.x + label!.width).toBeLessThan(entry!.x);
  });
});

test.describe('手机设置目录', () => {
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true });

  test('点按各组并返回底部导航中的设置', async ({ page }) => {
    await visitSettings(page, (target) => target.tap());
    const nav = await page.getByRole('navigation', { name: '主导航' }).boundingBox();
    expect(nav!.y + nav!.height).toBe(844);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(390);
  });
});

for (const device of readerDevices) {
  test.describe(device.name + '外观', () => {
    test.use({ viewport: device.viewport, hasTouch: device.hasTouch, colorScheme: 'dark' });

    test('跟随系统、固定外观与刷新恢复覆盖登录、列表和筛选弹层', async ({ page }) => {
      const root = page.locator('html');
      const appearance = page.getByRole('group', { name: '外观' });
      const day = appearance.getByRole('button', { name: '日间' });
      const night = appearance.getByRole('button', { name: '夜间' });
      const system = appearance.getByRole('button', { name: '跟随系统' });
      await page.goto('./#/settings');
      await expect(page.getByLabel('访问密码')).toBeVisible();
      await expect(root).toHaveCSS('color-scheme', 'dark');
      await page.getByLabel('访问密码').fill('finevent-e2e-password-only');
      await device.activate(page.getByRole('button', { name: '进入工作台' }));
      await expect(system).toHaveAttribute('aria-pressed', 'true');
      await page.emulateMedia({ colorScheme: 'light' });
      await expect(root).toHaveCSS('color-scheme', 'light');

      await device.activate(night);
      await expect(night).toHaveAttribute('aria-pressed', 'true');
      await expect(root).toHaveCSS('color-scheme', 'dark');
      const paper = await root.evaluate((el) =>
        getComputedStyle(el).getPropertyValue('--paper').trim(),
      );
      await expect(page.locator('meta[name="theme-color"]')).toHaveAttribute('content', paper);
      await page.reload();
      await expect(night).toHaveAttribute('aria-pressed', 'true');
      const nav = page.getByRole('navigation', { name: '主导航' });
      await device.activate(nav.getByRole('link', { name: '报道', exact: true }));
      const advanced = page.getByRole('button', { name: '高级搜索', exact: true });
      await device.activate(advanced);
      const sort = page.getByRole('combobox', { name: '排序' });
      await expect(page.locator('.reader-advanced .q-field--dark')).toHaveCount(6);
      await sort.click();
      await expect(page.locator('.q-menu--dark').getByRole('listbox')).toBeVisible();
      await page.getByRole('option', { name: '来源发布优先', exact: true }).click();
      await device.activate(page.getByRole('button', { name: '应用筛选' }));
      await expect(advanced).toBeFocused();
      await expect(page.getByRole('button', { name: '移除排序条件：来源发布优先' })).toBeVisible();

      await device.activate(nav.getByRole('link', { name: '设置', exact: true }));
      await device.activate(day);
      await page.emulateMedia({ colorScheme: 'dark' });
      await page.reload();
      await expect(day).toHaveAttribute('aria-pressed', 'true');
      await expect(root).toHaveCSS('color-scheme', 'light');
      await device.activate(system);
      await expect(root).toHaveCSS('color-scheme', 'dark');
      await page.emulateMedia({ colorScheme: 'light' });
      await expect(root).toHaveCSS('color-scheme', 'light');
      await page.reload();
      await expect(system).toHaveAttribute('aria-pressed', 'true');
    });
  });
}

const recordPages = [
  {
    title: '报道通知记录',
    path: '/settings/news-notifications',
    api: 'news-push',
    retry: '重试读取推送记录',
    region: '报道通知记录',
  },
  {
    title: '提醒记录',
    path: '/settings/reminder-history',
    api: 'reminders',
    retry: '重试提醒记录',
    region: '自动提醒记录',
  },
  {
    title: 'AI 解读记录',
    path: '/settings/ai-history',
    api: 'ai',
    retry: '刷新解读记录',
    region: '最近解读任务',
  },
];
for (const record of recordPages) {
  test(record.title + '：独立链接支持重试、重载并返回设置', async ({ page }) => {
    const endpoint = '**/api/v1/' + record.api;
    await page.route(endpoint, (route) =>
      route.fulfill({ status: 503, json: { error: '记录暂不可用' } }),
    );
    await signIn(page, record.path);
    await expect(page.getByRole('alert')).toContainText('记录暂不可用');
    await page.unroute(endpoint);
    await page.getByRole('button', { name: record.retry }).click();
    await expect(page.getByRole('region', { name: record.region, exact: true })).toBeVisible();
    await page.reload();
    await expect(
      page.getByRole('heading', { name: record.title, level: 1, exact: true }),
    ).toBeFocused();
    await expect(page.getByRole('region', { name: record.region, exact: true })).toBeVisible();
    await expect(page.locator('main form')).toHaveCount(0);
    const length = await page.evaluate(() => history.length);
    await page.getByRole('link', { name: '返回设置', exact: true }).click();
    await expect(page.getByRole('heading', { name: '设置', exact: true })).toBeFocused();
    expect(await page.evaluate(() => history.length)).toBe(length);
  });
}
