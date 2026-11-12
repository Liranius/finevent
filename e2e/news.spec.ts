import { expect, test, type Page } from '@playwright/test';

import type { NewsDetail, NewsEntry, NewsMeta, NewsPage } from '../apps/web/src/news.js';

// UI fixtures only: the Go tests exercise the real storage, migration and JSON routes.
async function newsFixtures(page: Page) {
  const now = Math.floor(Date.now() / 1000);
  const item: NewsEntry = {
    id: 101,
    source: 'wallstreetcn',
    source_id: 'test-1',
    title: '测试报道：央行调整货币政策，银行关注流动性安排',
    content: '测试来源正文。<img src=x onerror="alert(1)">\n所有数据均为浏览器测试夹具。',
    url: 'https://example.org/news/101',
    published_at: now - 80,
    first_seen: now - 10,
    last_seen: now,
    source_observed_at: now,
    ingested_at: now - 9,
    updated_at: now,
    version: 2,
    baseline: false,
    classification: {
      categories: ['monetary'],
      industries: ['finance'],
      importance: 3,
      reasons: ['货币与流动性：命中“央行”', '金融：提及“银行”'],
      rule_version: 'topics-v1',
    },
  };
  const other: NewsEntry = {
    ...item,
    id: 102,
    source: 'trendradar:cls-telegraph',
    source_id: 'test-2',
    title: '测试报道：央行调整货币政策，银行关注流动性安排。',
    content: '',
    url: 'javascript:alert(1)',
    published_at: 0,
    ingested_at: 0,
    updated_at: 0,
    version: 1,
    classification: null,
  };
  const info: NewsMeta = {
    categories: [{ id: 'monetary', name: '货币与流动性' }],
    industries: [{ id: 'finance', name: '金融' }],
    sources: ['wallstreetcn', 'trendradar:cls-telegraph'],
    rule_version: 'topics-v1',
    server_time: now,
    collection_enabled: true,
    trendradar_enabled: true,
    channels: [
      {
        id: 'wallstreetcn',
        last_attempt: now,
        last_success: now - 120,
        success: false,
        error: '测试：上游暂时不可用',
        gap: '测试：窗口未补齐',
        gap_at: now - 3600,
      },
      {
        id: 'trendradar',
        last_attempt: now,
        last_success: now,
        success: true,
        error: '',
        gap: '',
        gap_at: 0,
      },
    ],
  };
  const state = { changed: false, fail: false, queries: [] as URLSearchParams[] };
  await page.route('**/api/v1/news**', async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.split('/').pop();
    if (path === 'calendar-links') return route.fulfill({ json: { items: [], next: 0 } });
    if (path === 'analysis') return route.fulfill({ json: { job: null } });
    if (path === 'meta') return route.fulfill({ json: info });
    if (path === 'changes')
      return route.fulfill({
        json: { watermark: state.changed ? 2 : 1, changed: state.changed ? 1 : 0, reset: false },
      });
    if (path === 'news') {
      if (state.fail)
        return route.fulfill({ status: 503, json: { error: '测试：新闻读取暂时失败' } });
      state.queries.push(url.searchParams);
      const q = url.searchParams.get('q');
      const result: NewsPage = {
        items: q === '无匹配' ? [] : url.searchParams.has('cursor') ? [other] : [item],
        next: q === '无匹配' || url.searchParams.has('cursor') ? '' : 'page-two',
        watermark: state.changed ? 2 : 1,
        backlog: 0,
      };
      return route.fulfill({ json: result });
    }
    if (path === '101' || path === '102') {
      const detail: NewsDetail = {
        item: path === '101' ? item : other,
        revisions:
          path === '101'
            ? [
                {
                  version: 1,
                  title: '测试报道旧标题',
                  content: '旧正文',
                  url: null,
                  published_at: null,
                  saved_at: now - 60,
                },
              ]
            : [],
        related: path === '101' ? [{ item: other, kind: 'similar_title' }] : [],
        revision_more: false,
        relation_scope: '相似标题仅供核对，保留各来源证据。',
      };
      return route.fulfill({ json: detail });
    }
    return route.fulfill({ status: 404, json: { error: '报道不存在或已超过保留期' } });
  });
  return { state, item, other };
}
async function loginNews(page: Page, hash = 'news') {
  await page.goto(`./#${hash}`);
  await page.getByLabel('访问密码').fill('finevent-e2e-password-only');
  await page.getByRole('button', { name: '进入工作台' }).click();
  await expect(page.getByRole('heading', { name: '新闻事件流', exact: true })).toBeVisible();
}

test('新闻：筛选、详情、跨源证据、修订、键盘与窄屏', async ({ page }, testInfo) => {
  const { state, item, other } = await newsFixtures(page);
  await page.setViewportSize({ width: 1280, height: 900 });
  await loginNews(page);
  await page.getByRole('combobox', { name: '类别', exact: true }).selectOption('monetary');
  await page.getByLabel('提及行业').selectOption('finance');
  await page.getByLabel('排序').selectOption('published');
  await page.getByRole('button', { name: '应用筛选' }).click();
  await expect.poll(() => state.queries.at(-1)?.get('category')).toBe('monetary');
  expect(state.queries.at(-1)?.get('industry')).toBe('finance');
  expect(state.queries.at(-1)?.get('order')).toBe('published');
  await page.screenshot({ path: testInfo.outputPath('news-desktop.png'), fullPage: true });
  const title = page.getByRole('button', { name: item.title, exact: true });
  await title.focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('heading', { name: item.title, exact: true })).toBeFocused();
  await expect(page.getByRole('link', { name: '查看新闻原文 ↗' })).toHaveAttribute(
    'href',
    item.url,
  );
  await expect(page.locator('.news-body > .news-copy')).toContainText('<img');
  await expect(page.locator('.news-copy img')).toHaveCount(0);
  await page.getByText('查看分类依据', { exact: true }).click();
  await expect(page.getByText('货币与流动性：命中“央行”', { exact: true })).toBeVisible();
  await page.getByText('历史修订 · 1', { exact: true }).click();
  await expect(
    page.getByRole('heading', { name: '第 1 版 · 测试报道旧标题', exact: true }),
  ).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('news-detail-desktop.png'), fullPage: true });
  await page.setViewportSize({ width: 393, height: 852 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.getByRole('button', { name: other.title, exact: true }).click();
  await expect(page.getByRole('heading', { name: other.title, exact: true })).toBeFocused();
  await expect(page.getByRole('link', { name: '查看新闻原文 ↗' })).toHaveCount(0);
  await expect(page.getByText('未知 · 旧数据未记录', { exact: true })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('news-mobile-detail.png'), fullPage: true });
  await page.getByRole('button', { name: '← 返回新闻列表' }).click();
  await expect(page.getByRole('heading', { name: '新闻事件流', exact: true })).toBeFocused();
  await expect(page.getByRole('combobox', { name: '类别', exact: true })).toHaveValue('monetary');
  await page.getByText('采集通道状态', { exact: true }).click();
  await expect(page.getByText('测试：上游暂时不可用', { exact: true })).toBeVisible();
  await expect(page.getByText(/后续成功不代表该缺口已经补齐/)).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('news-mobile.png'), fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.getByRole('button', { name: '财经日历', exact: true }).click();
  await expect(page.getByRole('heading', { name: '财经日历', exact: true })).toBeVisible();
});

test('新闻：增量提示保留未提交筛选、分页与空状态', async ({ page }) => {
  const { state, other } = await newsFixtures(page);
  await page.clock.install();
  await loginNews(page);
  await page.getByRole('button', { name: '加载更多报道' }).click();
  await expect(page.getByRole('button', { name: other.title, exact: true })).toBeVisible();
  await page.getByLabel('标题或正文').fill('未提交文字');
  state.changed = true;
  await page.clock.fastForward(31000);
  await expect(page.getByRole('button', { name: '更新当前内容' })).toBeVisible();
  await expect(page.getByLabel('标题或正文')).toHaveValue('未提交文字');
  await page.getByRole('button', { name: '更新当前内容' }).click();
  await expect(page.getByRole('button', { name: '更新当前内容' })).toHaveCount(0);
  expect(state.queries.at(-1)?.get('q')).toBe('');
  await page.getByLabel('标题或正文').fill('无匹配');
  await page.getByLabel('标题或正文').press('Enter');
  await expect(page.getByText(/没有符合筛选的报道/)).toBeVisible();
});

test('新闻：失败重试、直接详情链接及不存在的记录', async ({ page }) => {
  const { state, item } = await newsFixtures(page);
  state.fail = true;
  await loginNews(page);
  await expect(page.getByRole('alert')).toContainText('新闻读取暂时失败');
  state.fail = false;
  await page.getByRole('button', { name: '重试新闻' }).click();
  await expect(page.getByRole('button', { name: item.title, exact: true })).toBeVisible();
  state.fail = true;
  await page.goto('./#news=101');
  await page.reload();
  await expect(page.getByRole('heading', { name: item.title, exact: true })).toBeFocused();
  await page.getByRole('button', { name: '← 返回新闻列表' }).click();
  await expect(page.getByRole('alert')).toContainText('新闻读取暂时失败');
  state.fail = false;
  await page.getByRole('button', { name: '重试新闻' }).click();
  await expect(page.getByRole('button', { name: item.title, exact: true })).toBeVisible();
  await page.goto('./#news=999999');
  await expect(page.getByRole('alert')).toContainText('报道不存在或已超过保留期');
  await page.getByRole('button', { name: '← 返回新闻列表' }).click();
  await expect(page.getByRole('button', { name: item.title, exact: true })).toBeVisible();
});
