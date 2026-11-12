import { expect, test, type Page } from '@playwright/test';

import type { CalendarLink } from '../apps/web/src/calendar-links.js';
import type { CalendarEvent } from '../apps/web/src/calendar.js';
import type { NewsEntry } from '../apps/web/src/news.js';

// Browser fixtures exercise interaction; Go tests cover actual transactions and persistence.
async function setup(page: Page) {
  const event: CalendarEvent = {
    id: 'link-event',
    source: 'manual',
    source_id: 'fixture',
    title: '测试日程：美国 CPI 发布',
    kind: 'other',
    start: '2026-11-05',
    end: '2026-11-05',
    timezone: 'Asia/Shanghai',
    precision: 'date',
    status: 'confirmed',
    importance: 2,
    url: 'https://example.org/calendar',
    evidence: '测试日程依据：11 月 5 日发布 CPI。',
    fund_code: '',
    conditions: '',
    version: 1,
    updated_at: 1,
  };
  const news: NewsEntry = {
    id: 501,
    source: 'wallstreetcn',
    source_id: 'fixture',
    title: '测试报道：美国 CPI 发布安排',
    content: '测试新闻正文。报道明确讨论本次发布安排。',
    url: 'https://example.org/news',
    published_at: 0,
    first_seen: 1,
    last_seen: 1,
    source_observed_at: 0,
    ingested_at: 1,
    updated_at: 1,
    version: 1,
    baseline: false,
    classification: null,
  };
  const state = {
    event,
    news,
    links: [] as CalendarLink[],
    fail: false,
    conflict: false,
    searches: [] as URLSearchParams[],
    queries: [] as URLSearchParams[],
  };
  await page.route('**/api/v1/news**', async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path.endsWith('/calendar-links') || path.includes('/calendar-links/')) {
      if (state.fail)
        return route.fulfill({ status: 503, json: { error: '测试：关联服务暂时不可用' } });
      if (route.request().method() === 'PUT') {
        if (state.conflict)
          return route.fulfill({
            status: 409,
            json: { error: '报道、日程或关联已变更，请刷新后重新核对' },
          });
        const input = route.request().postDataJSON() as { note: string };
        state.links = [
          {
            id: 2,
            news: structuredClone(news),
            event: structuredClone(event),
            note: input.note,
            confirmed_at: 1,
            news_snapshot: structuredClone(news),
            event_snapshot: structuredClone(event),
            changed: false,
          },
        ];
        return route.fulfill({ json: { saved: true } });
      }
      if (route.request().method() === 'DELETE') {
        state.links = [];
        return route.fulfill({ json: { saved: true } });
      }
      return route.fulfill({ json: { items: state.links, next: 0 } });
    }
    if (path.endsWith('/analysis')) return route.fulfill({ json: { job: null } });
    if (path.endsWith('/meta'))
      return route.fulfill({
        json: {
          categories: [],
          industries: [],
          sources: ['wallstreetcn'],
          channels: [],
          server_time: 1,
          rule_version: 'test',
          collection_enabled: false,
          trendradar_enabled: false,
        },
      });
    if (path.endsWith('/changes'))
      return route.fulfill({ json: { changed: 0, watermark: 1, reset: false } });
    if (path.endsWith('/501'))
      return route.fulfill({
        json: {
          item: news,
          revisions: [],
          related: [],
          revision_more: false,
          relation_scope: '测试',
        },
      });
    if (path.endsWith('/news')) {
      state.queries.push(url.searchParams);
      return route.fulfill({ json: { items: [news], next: '', watermark: 1, backlog: 0 } });
    }
    return route.fallback();
  });
  await page.route('**/api/v1/calendar-link-search?**', (route) => {
    const q = new URL(route.request().url()).searchParams;
    state.searches.push(q);
    return route.fulfill({ json: { items: q.get('q') === '无匹配' ? [] : [event], more: false } });
  });
  await page.route('**/api/v1/calendar/link-event', (route) =>
    route.fulfill({ json: { event, revisions: [] } }),
  );
  await page.route('**/api/v1/calendar/link-event/news-links?**', (route) =>
    route.fulfill({ json: { items: state.links, next: 0 } }),
  );
  await page.route('**/api/v1/reminders/events/link-event', (route) =>
    route.fulfill({
      json: {
        event_id: event.id,
        followed: false,
        override: null,
        settings_enabled: false,
        plans: [],
      },
    }),
  );
  await page.goto('./#news');
  await page.getByLabel('访问密码').fill('finevent-e2e-password-only');
  await page.getByRole('button', { name: '进入工作台' }).click();
  await page.getByLabel('标题或正文').fill('CPI');
  await page.getByRole('button', { name: '应用筛选' }).click();
  await page.getByRole('button', { name: news.title, exact: true }).click();
  return state;
}

test('日程关联：核对、双向详情、筛选保留、解除与窄屏键盘', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  const state = await setup(page);
  const panel = page.getByRole('region', { name: '关联日程', exact: true });
  await expect(panel.getByText('尚无已确认关联。可查找日程并填写依据。')).toBeVisible();
  await panel.getByRole('button', { name: '查找日程', exact: true }).click();
  await expect(panel.getByText(/今天（来源发布时间未知）/)).toBeVisible();
  await panel.getByLabel('日程开始范围').fill('2026-11-01');
  await panel.getByLabel('日程结束范围').fill('2026-11-10');
  await panel.getByLabel('日程标题或基金代码').fill('无匹配');
  await panel.getByRole('button', { name: '查询日程' }).click();
  await expect(panel.getByText('此范围没有匹配日程，请调整日期或关键词。')).toBeVisible();
  await panel.getByLabel('日程标题或基金代码').fill('CPI');
  await panel.getByRole('button', { name: '查询日程' }).click();
  await panel.getByRole('button', { name: '核对此日程' }).focus();
  await page.keyboard.press('Enter');
  await expect(panel.getByLabel('关联依据')).toBeFocused();
  await expect(panel.getByRole('button', { name: '确认关联', exact: true })).toBeDisabled();
  await panel.getByLabel('关联依据').fill('报道中的国家、指标与本次发布月份一致，已核对原文。');
  await panel.getByLabel('关联依据').press('Tab');
  await expect(panel.getByRole('button', { name: '确认关联', exact: true })).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(panel.getByRole('heading', { name: '关联日程', exact: true })).toBeFocused();
  await panel.getByText(/确认依据 ·/).click();
  await expect(panel.getByText('测试日程依据：11 月 5 日发布 CPI。')).toBeVisible();
  await panel.screenshot({ path: testInfo.outputPath('calendar-links-desktop.png') });
  await panel.getByRole('link', { name: state.event.title }).click();
  await expect(page.getByRole('heading', { name: '事件详情', exact: true })).toBeFocused();
  await page
    .getByRole('region', { name: '相关报道', exact: true })
    .getByRole('link', { name: state.news.title })
    .click();
  await expect(page.getByRole('heading', { name: state.news.title, exact: true })).toBeFocused();
  await page.getByRole('button', { name: '返回新闻列表' }).click();
  await expect(page.getByLabel('标题或正文')).toHaveValue('CPI');
  expect(state.queries.at(-1)?.get('q')).toBe('CPI');
  await page.getByRole('button', { name: state.news.title, exact: true }).click();
  await page.setViewportSize({ width: 393, height: 852 });
  await panel.getByRole('button', { name: '重新核对关联' }).click();
  await expect(panel.getByLabel('关联依据')).toBeFocused();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
  await panel.screenshot({ path: testInfo.outputPath('calendar-links-mobile.png') });
  await panel.getByRole('button', { name: '取消核对' }).click();
  await panel.getByRole('button', { name: '解除关联', exact: true }).click();
  await panel.getByRole('button', { name: '保留关联' }).click();
  await expect(panel.getByRole('link', { name: state.event.title })).toBeVisible();
  await panel.getByRole('button', { name: '解除关联', exact: true }).click();
  await panel.getByRole('button', { name: '确认解除', exact: true }).click();
  await expect(panel.getByText('尚无已确认关联。可查找日程并填写依据。')).toBeVisible();
});

test('日程关联：修订提示、冲突保留输入、刷新失败与恢复', async ({ page }) => {
  const state = await setup(page);
  const panel = page.getByRole('region', { name: '关联日程', exact: true });
  state.links = [
    {
      id: 1,
      news: state.news,
      event: { ...state.event, version: 2, status: 'cancelled' },
      news_snapshot: { ...state.news },
      event_snapshot: { ...state.event },
      note: '测试先前人工确认的关联依据',
      confirmed_at: 1,
      changed: true,
    },
  ];
  await panel.getByRole('button', { name: '刷新关联' }).click();
  await expect(panel.getByText('报道或日程已有修订，关联待重新核对。')).toBeVisible();
  await expect(panel.getByText(/手工补录 · 已取消/)).toBeVisible();
  await panel.getByRole('button', { name: '重新核对关联' }).click();
  await panel.getByLabel('关联依据').fill('测试：这份输入在冲突后应保留。');
  state.conflict = true;
  await panel.getByRole('button', { name: '确认关联', exact: true }).click();
  await expect(panel.getByRole('alert')).toContainText('已变更');
  await expect(panel.getByLabel('关联依据')).toHaveValue('测试：这份输入在冲突后应保留。');
  await panel.getByRole('button', { name: '取消核对' }).click();
  state.fail = true;
  await panel.getByRole('button', { name: '刷新关联' }).click();
  await expect(panel.getByRole('alert')).toContainText('暂时不可用');
  await expect(panel.getByRole('link', { name: state.event.title })).toBeVisible();
  state.fail = false;
  await panel.getByRole('button', { name: '刷新关联' }).click();
  await expect(panel.getByRole('alert')).toHaveCount(0);
});
