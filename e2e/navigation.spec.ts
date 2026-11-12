import { expect, test, type Page } from '@playwright/test';

import { blankEvent, type CalendarEvent, type FundDraft } from '../apps/web/src/calendar.js';
import type { NewsEntry } from '../apps/web/src/news.js';

async function login(page: Page, link = './') {
  await page.goto(link);
  await page.getByLabel('访问密码').fill('finevent-e2e-password-only');
  await page.getByRole('button', { name: '进入工作台' }).click();
  await expect(page.getByRole('navigation', { name: '主导航', exact: true })).toBeVisible();
}

async function newsFixture(page: Page) {
  const item: NewsEntry = {
    id: 701,
    source: 'wallstreetcn',
    source_id: 'navigation-test',
    title: '导航测试报道',
    content: '导航测试来源正文。',
    url: 'https://example.org/news/701',
    published_at: 0,
    first_seen: 1,
    last_seen: 1,
    source_observed_at: 1,
    ingested_at: 1,
    updated_at: 1,
    version: 1,
    baseline: false,
    classification: null,
  };
  await page.route('**/api/v1/news**', (route) => {
    const path = new URL(route.request().url()).pathname.split('/').pop();
    if (path === 'meta')
      return route.fulfill({
        json: {
          categories: [],
          industries: [],
          sources: [],
          rule_version: 'test',
          collection_enabled: false,
          trendradar_enabled: false,
          server_time: 1,
          channels: [],
        },
      });
    if (path === 'news')
      return route.fulfill({ json: { items: [item], next: '', watermark: 1, backlog: 0 } });
    if (path === '701')
      return route.fulfill({
        json: { item, revisions: [], related: [], revision_more: false, relation_scope: '' },
      });
    if (path === 'calendar-links') return route.fulfill({ json: { items: [], next: 0 } });
    if (path === 'analysis') return route.fulfill({ json: { job: null } });
    return route.continue();
  });
}

async function createEvent(page: Page) {
  const response = await page.request.post('api/v1/calendar', {
    headers: { Origin: new URL(page.url()).origin },
    data: {
      ...blankEvent(),
      title: '导航测试日程',
      kind: 'other',
      start: '2026-11-09',
      end: '2026-11-09',
      precision: 'date',
      url: 'https://example.org/calendar',
      evidence: '导航测试依据。',
    },
  });
  expect(response.ok()).toBe(true);
  return (await response.json()) as CalendarEvent;
}

test('工作区与验证页：浏览器前进后退、刷新、键盘和默认首页', async ({ page }) => {
  await login(page);
  await expect(page).toHaveURL(/#\/news$/);
  await page.getByRole('link', { name: '财经日历', exact: true }).click();
  await page.getByRole('link', { name: '报道', exact: true }).focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('#news-heading')).toBeFocused();
  await page.getByRole('link', { name: '设置', exact: true }).click();
  await page.getByRole('link', { name: 'AI 设置与预算', exact: true }).click();
  await expect(page.locator('#ai-heading')).toBeFocused();
  await page.getByRole('link', { name: '设置', exact: true }).click();
  await page.getByRole('link', { name: '设备、测试通知与诊断', exact: true }).click();
  await expect(page).toHaveURL(/#\/verification\/push$/);
  await page.getByRole('button', { name: '02 采集时效' }).click();
  await page.getByRole('button', { name: '03 资源共存' }).click();
  await page.goBack();
  await expect(page.locator('#sources-title')).toBeVisible();
  await page.reload();
  await expect(page).toHaveURL(/#\/verification\/sources$/);
  await expect(page.getByRole('heading', { name: '开发者功能', exact: true })).toBeFocused();
  await page.goForward();
  await expect(page.locator('#resources-title')).toBeVisible();
  await page.goBack();
  await page.goBack();
  await expect(page.locator('#push-title')).toBeVisible();
  await page.goBack();
  await expect(page.getByRole('link', { name: '设备、测试通知与诊断', exact: true })).toBeFocused();
  await page.goBack();
  await expect(page.locator('#ai-heading')).toBeFocused();
  await page.goBack();
  await expect(page.getByRole('link', { name: 'AI 设置与预算', exact: true })).toBeFocused();
  await page.goBack();
  await expect(page.locator('#news-heading')).toBeFocused();
  await page.goBack();
  await expect(page.getByRole('heading', { name: '财经日历', exact: true })).toBeFocused();
});

test('日历：旧通知链接只占一次历史，返回保留筛选、月份和焦点', async ({ page }, testInfo) => {
  await page.clock.setFixedTime(new Date('2026-11-09T04:00:00Z'));
  await login(page, './#/calendar');
  const event = await createEvent(page);
  await page.reload();
  await page.getByLabel('事件类别').selectOption(event.kind);
  await page.getByRole('button', { name: '议程', exact: true }).click();
  await page.evaluate((id) => {
    window.location.hash = `event=${id}`;
  }, event.id);
  await expect(page).toHaveURL(new RegExp(`#/calendar/events/${event.id}$`));
  await expect(page.getByRole('heading', { name: event.title, exact: true })).toBeFocused();
  await page.goBack();
  await expect(page).toHaveURL(/#\/calendar$/);
  await expect(page.getByLabel('事件类别')).toHaveValue(event.kind);
  await expect(page.locator(`#calendar-event-${event.id}`)).toBeFocused();
  await page.goForward();
  await expect(page.getByRole('heading', { name: event.title, exact: true })).toBeVisible();
  await page.getByRole('button', { name: '返回财经日历', exact: true }).click();
  await expect(page.locator(`#calendar-event-${event.id}`)).toBeFocused();
  await page.getByLabel('事件类别').selectOption('macro');
  await page.evaluate((id) => {
    window.location.hash = `event=${id}`;
  }, event.id);
  await expect(page.getByRole('heading', { name: event.title, exact: true })).toBeFocused();
  await page.getByRole('button', { name: '返回财经日历', exact: true }).click();
  await expect(page.getByRole('heading', { name: '财经日历', exact: true })).toBeFocused();
  await page.getByRole('button', { name: '下个月' }).click();
  await page.getByRole('link', { name: '报道', exact: true }).click();
  await page.goBack();
  await expect(page.getByRole('heading', { name: '2026 年 12 月' })).toBeVisible();
  await expect(page.getByLabel('事件类别')).toHaveValue('macro');
  await page.screenshot({
    path: testInfo.outputPath('router-desktop-calendar.png'),
    fullPage: true,
  });
});

test('新闻：登录前旧深链接、直接返回、历史恢复与窄屏键盘', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 393, height: 852 });
  await newsFixture(page);
  await login(page, './#news=701');
  await expect(page).toHaveURL(/#\/news\/701$/);
  await expect(page.locator('#news-detail-heading')).toBeFocused();
  const historyLength = await page.evaluate(() => window.history.length);
  await page.getByRole('button', { name: '返回报道列表' }).click();
  await expect(page).toHaveURL(/#\/news$/);
  expect(await page.evaluate(() => window.history.length)).toBe(historyLength);
  await page.getByLabel('标题或正文').fill('导航');
  await page.getByLabel('标题或正文').press('Enter');
  await page.getByRole('button', { name: '导航测试报道', exact: true }).focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('#news-detail-heading')).toBeFocused();
  await page.goBack();
  await expect(page.locator('#news-item-701')).toBeFocused();
  await expect(page.getByLabel('标题或正文')).toHaveValue('导航');
  await page.goForward();
  await expect(page.locator('#news-detail-heading')).toBeFocused();
  await page.getByRole('link', { name: '设置', exact: true }).click();
  await page.getByRole('link', { name: '报道关注与推送', exact: true }).focus();
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/#\/news\/push$/);
  await expect(page.locator('#news-settings-heading')).toBeFocused();
  await page.goBack();
  await expect(page.getByRole('link', { name: '报道关注与推送', exact: true })).toBeFocused();
  await page.goBack();
  await expect(page.locator('#news-detail-heading')).toBeFocused();
  await page.getByRole('link', { name: '财经日历', exact: true }).click();
  await page.goBack();
  await expect(page.locator('#news-detail-heading')).toBeFocused();
  await page.getByRole('button', { name: '返回报道列表' }).click();
  await expect(page.getByLabel('标题或正文')).toHaveValue('导航');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
  await page.screenshot({ path: testInfo.outputPath('router-mobile-news.png'), fullPage: true });
});

test('草稿与失败路径：已打开应用接收旧链接、刷新、重试与无效地址', async ({ page }) => {
  await login(page);
  const response = await page.request.post('api/v1/fund-drafts', {
    headers: { Origin: new URL(page.url()).origin },
    data: {
      text: '基金代码：123456。开放赎回时间为2026年11月2日至2026年11月6日。',
      url: 'https://example.org/fund',
    },
  });
  expect(response.ok()).toBe(true);
  const draft = (await response.json()) as FundDraft;
  await page.getByRole('link', { name: '设置', exact: true }).click();
  await page.getByRole('link', { name: 'AI 设置与预算', exact: true }).click();
  await page.evaluate((id) => {
    window.location.hash = `draft=${id}`;
  }, draft.id);
  await expect(page).toHaveURL(new RegExp(`#/calendar/fund/${draft.id}$`));
  await expect(page.getByLabel('基金代码', { exact: true })).toHaveValue('123456');
  await page.goBack();
  await expect(page.locator('#ai-heading')).toBeVisible();
  await page.goForward();
  await page.reload();
  await expect(page.getByLabel('基金代码', { exact: true })).toHaveValue('123456');
  const event = await createEvent(page);
  await page.route(`**/api/v1/calendar/${event.id}`, (route) =>
    route.fulfill({ status: 503, json: { error: '导航测试暂时失败' } }),
  );
  await page.evaluate((id) => {
    window.location.hash = `event=${id}`;
  }, event.id);
  await expect(page.getByRole('alert')).toContainText('导航测试暂时失败');
  await page.unroute(`**/api/v1/calendar/${event.id}`);
  await page.getByRole('button', { name: '重试页面' }).click();
  await expect(page.getByRole('heading', { name: event.title, exact: true })).toBeVisible();
  await page.evaluate(() => {
    window.location.hash = '/unknown-page';
  });
  await expect(page.getByRole('heading', { name: '找不到这个页面' })).toBeFocused();
  await page.getByRole('link', { name: '返回财经日历' }).click();
  await expect(page).toHaveURL(/#\/calendar$/);
});

test('快速切换事件：迟到的详情响应不覆盖当前位置和焦点', async ({ page }) => {
  await login(page);
  const event = await createEvent(page);
  let release!: () => void;
  const pending = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route('**/api/v1/calendar/slow-event', async (route) => {
    await pending;
    await route.fulfill({
      json: { event: { ...event, id: 'slow-event', title: '迟到的旧详情' }, revisions: [] },
    });
  });
  const requested = page.waitForRequest('**/api/v1/calendar/slow-event');
  await page.evaluate(() => {
    window.location.hash = 'event=slow-event';
  });
  await requested;
  await page.evaluate((id) => {
    window.location.hash = `event=${id}`;
  }, event.id);
  await expect(page.getByRole('heading', { name: event.title, exact: true })).toBeVisible();
  const responded = page.waitForResponse('**/api/v1/calendar/slow-event');
  release();
  await responded;
  await expect(page.getByRole('heading', { name: '迟到的旧详情' })).toHaveCount(0);
  await expect(page.getByRole('heading', { name: event.title, exact: true })).toBeFocused();
});
