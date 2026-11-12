import { expect, test, type Page } from '@playwright/test';

import type { NewsDetail, NewsEntry, NewsMeta, NewsPage } from '../apps/web/src/news.js';
import { readerDevices } from './reader-devices.js';

// UI fixtures only: the Go tests exercise the real storage, migration and JSON routes.
async function newsFixtures(page: Page, pageLength = 0) {
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
  const paginated = Array.from({ length: 2 * pageLength }, (_, index) => ({
    ...item,
    id: 1000 + index,
    title: '分页报道 ' + (index + 1),
    first_seen: now - index,
  }));
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
    if (path === 'changes') {
      const watermark = state.changed ? 2 : 1;
      const after = Number(url.searchParams.get('after'));
      return route.fulfill({
        json: { watermark, changed: watermark > after ? 1 : 0, reset: after > watermark },
      });
    }
    if (path === 'news') {
      if (state.fail)
        return route.fulfill({ status: 503, json: { error: '测试：新闻读取暂时失败' } });
      state.queries.push(url.searchParams);
      const q = url.searchParams.get('q');
      if (pageLength) {
        const offset = Number(url.searchParams.get('cursor') ?? 0);
        const latest = state.changed
          ? [
              ...Array.from({ length: 27 }, (_, index) => ({
                ...item,
                id: 2000 + index,
                title: '新增报道 ' + index,
                first_seen: now + 27 - index,
              })),
              ...paginated,
            ]
          : paginated;
        return route.fulfill({
          json: {
            items: latest.slice(offset, offset + pageLength),
            next: offset + pageLength < latest.length ? String(offset + pageLength) : '',
            watermark: state.changed ? 2 : 1,
            backlog: 0,
          } satisfies NewsPage,
        });
      }
      const result: NewsPage = {
        items: q === '无匹配' ? [] : url.searchParams.has('cursor') ? [other] : [item],
        next: q === '无匹配' || url.searchParams.has('cursor') ? '' : 'page-two',
        watermark: state.changed ? 2 : 1,
        backlog: 0,
      };
      return route.fulfill({ json: result });
    }
    const paginatedItem = paginated.find((row) => String(row.id) === path);
    if (paginatedItem || path === '101' || path === '102') {
      const detail: NewsDetail = {
        item: paginatedItem ?? (path === '101' ? item : other),
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
  return { state, item, other, info };
}
async function loginNews(page: Page, hash = 'news') {
  await page.goto(`./#${hash}`);
  await page.getByLabel('访问密码').fill('finevent-e2e-password-only');
  await page.getByRole('button', { name: '进入工作台' }).click();
  await expect(page.getByRole('heading', { name: '报道记录', exact: true })).toBeVisible();
}

test('新闻：筛选、详情、跨源证据、修订、键盘与窄屏', async ({ page }, testInfo) => {
  const { state, item, other } = await newsFixtures(page);
  await page.setViewportSize({ width: 1280, height: 900 });
  await loginNews(page);
  await page.getByRole('button', { name: '高级搜索', exact: true }).click();
  for (const [field, value] of [
    ['类别', '货币与流动性'],
    ['提及行业', '金融'],
    ['排序', '来源发布优先'],
  ] as const) {
    await page.getByRole('combobox', { name: field, exact: true }).click();
    await page.getByRole('option', { name: value, exact: true }).click();
  }
  await page.getByRole('button', { name: '应用筛选', exact: true }).click();
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
  await page.getByRole('button', { name: '← 返回报道列表' }).click();
  await expect(page.locator('#news-item-101')).toBeFocused();
  await expect(page.getByRole('button', { name: '移除类别条件：货币与流动性' })).toBeVisible();
  await page.getByRole('link', { name: '设置', exact: true }).click();
  await page.getByRole('link', { name: '采集通道状态', exact: true }).click();
  await expect(page.getByText('测试：上游暂时不可用', { exact: true })).toBeVisible();
  await expect(page.getByText(/后续成功不代表该缺口已经补齐/)).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('news-mobile.png'), fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.getByRole('link', { name: '财经日历', exact: true }).click();
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
  await page.getByRole('button', { name: '← 返回报道列表' }).click();
  await expect(page.getByRole('alert')).toContainText('新闻读取暂时失败');
  state.fail = false;
  await page.getByRole('button', { name: '重试新闻' }).click();
  await expect(page.getByRole('button', { name: item.title, exact: true })).toBeVisible();
  await page.goto('./#news=999999');
  await expect(page.getByRole('alert')).toContainText('报道不存在或已超过保留期');
  await page.getByRole('button', { name: '← 返回报道列表' }).click();
  await expect(page.getByRole('button', { name: item.title, exact: true })).toBeVisible();
});

test('详情更新检查暂时失败时保留正文和分类，并可重试', async ({ page }) => {
  const { item } = await newsFixtures(page);
  await page.route(
    '**/api/v1/news/changes?**',
    (route) => route.fulfill({ status: 503, json: { error: '变更记录暂不可用' } }),
    { times: 1 },
  );
  await page.goto('./#/news/101');
  await page.getByLabel('访问密码').fill('finevent-e2e-password-only');
  await page.getByRole('button', { name: '进入工作台' }).click();
  await expect(page.getByRole('alert')).toContainText('变更记录暂不可用');
  await expect(page.locator('.news-body > .news-copy')).toHaveText(item.content);
  await expect(page.locator('.news-evidence')).toContainText('货币与流动性');
  await page.getByRole('button', { name: '重试更新检查' }).click();
  await expect(page.getByRole('alert')).toHaveCount(0);
  await expect(page.getByRole('button', { name: '更新当前内容' })).toHaveCount(0);
});

for (const device of readerDevices) {
  test.describe(`${device.name}报道`, () => {
    test.use({ viewport: device.viewport, hasTouch: device.hasTouch });

    test('切换排序后按北京时间分组，标明跨日发布及未知时间', async ({ page }, testInfo) => {
      await page.clock.setFixedTime(new Date('2026-11-12T04:00:00Z'));
      // All known timestamps are on November 11 in UTC, across midnight in Beijing.
      const seconds = (time: string) => Date.parse('2026-11-11T' + time + 'Z') / 1000;
      const { item: overnight, other: unknown, info } = await newsFixtures(page);
      info.industries = [
        { id: 'finance', name: '金融' },
        { id: 'tech', name: '科技' },
        { id: 'energy', name: '能源' },
      ];
      Object.assign(overnight, {
        title: '金融科技专题报告披露行业样本',
        source: 'trendradar:cls-telegraph',
        first_seen: seconds('16:04:32'),
        published_at: seconds('15:58:16'),
        classification: { ...overnight.classification, industries: ['finance', 'tech', 'energy'] },
      });
      Object.assign(unknown, {
        title: '市场观察汇总本周资金动向',
        source: 'trendradar:zaobao',
        first_seen: seconds('16:08:12'),
      });
      const sameDay = {
        ...overnight,
        id: 103,
        title: '银行公布开放日申购安排',
        source: 'wallstreetcn',
        first_seen: seconds('16:02:11'),
        published_at: seconds('16:01:48'),
        version: 1,
        classification: { ...overnight.classification, importance: 2, industries: [] },
      };
      await page.route('**/api/v1/news?**', (route) => {
        const order = new URL(route.request().url()).searchParams.get('order');
        return route.fulfill({
          json: {
            items:
              order === 'published' ? [sameDay, overnight, unknown] : [unknown, overnight, sameDay],
            next: '',
            watermark: 1,
            backlog: 0,
          },
        });
      });
      await loginNews(page);
      const headings = page.locator('.news-day > h2');
      const rows = page.locator('.news-title strong');
      const clocks = page.locator('.news-clock');
      const cross = page.getByRole('button', { name: overnight.title, exact: true });
      const desktopTimes = page.locator('.news-secondary-time:visible');
      const mobileNotes = page.locator('.news-earlier-publication:visible');
      await expect(headings).toHaveText(['2026/11/12']);
      await expect(rows).toHaveText([unknown.title, overnight.title, sameDay.title]);
      await expect(clocks).toHaveText(['00:08', '00:04', '00:02']);
      await expect(desktopTimes).toHaveText(
        device.hasTouch ? [] : ['发布时间未知', '发布 11月11日 23:58', '发布 00:01'],
      );
      await expect(mobileNotes).toHaveText(device.hasTouch ? ['11月11日发布'] : []);
      await expect(page.locator('.news-industries:visible')).toHaveText(
        device.hasTouch ? [] : ['提及：金融、科技'],
      );
      await expect(page.locator('.news-rule-important')).toHaveCount(1);
      await expect(cross).toContainText('重要 · 规则');
      await expect(cross).toContainText('财联社电报 · TrendRadar');
      await page.screenshot({ path: testInfo.outputPath('first-seen.png') });

      await device.activate(page.getByRole('button', { name: '高级搜索', exact: true }));
      const sort = page.getByRole('combobox', { name: '排序', exact: true });
      await sort.click();
      await page.getByRole('option', { name: '来源发布优先', exact: true }).click();
      await device.activate(page.getByRole('button', { name: '应用筛选', exact: true }));
      await expect(page.locator('.reader-advanced')).toHaveCount(0);
      await expect(page.getByRole('button', { name: '高级搜索', exact: true })).toBeFocused();
      await expect(headings).toHaveText(['2026/11/12', '2026/11/11', '发布时间未知']);
      await expect(rows).toHaveText([sameDay.title, overnight.title, unknown.title]);
      await expect(clocks).toHaveText(['00:01', '23:58', '未知']);
      await expect(desktopTimes).toHaveText(
        device.hasTouch
          ? []
          : ['首次记录 00:02', '首次记录 11月12日 00:04', '首次记录 2026/11/12 00:08'],
      );
      await expect(mobileNotes).toHaveCount(0);
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(
        device.viewport.width,
      );
      const lastRow = page.getByRole('button', { name: unknown.title, exact: true });
      await lastRow.evaluate((row) => row.scrollIntoView({ block: 'center' }));
      await expect(lastRow).toBeInViewport({ ratio: 1 });
      await page.screenshot({ path: testInfo.outputPath('published.png') });

      await device.activate(cross);
      const evidence = page.getByRole('complementary', { name: '分类与时间证据' });
      await expect(evidence).toContainText('金融 · 科技 · 能源');
      await expect(evidence).toContainText('上游首次记录');
      await expect(evidence).toContainText('2026/11/11 23:58:16');
      await device.activate(page.getByRole('button', { name: '返回报道列表' }));
      await expect(cross).toBeFocused();
      await device.activate(page.getByRole('button', { name: '移除排序条件：来源发布优先' }));
      await expect(headings).toHaveText(['2026/11/12']);
      await expect(rows).toHaveText([unknown.title, overnight.title, sameDay.title]);
    });

    test('直达详情检查修订，确认更新后读取新正文', async ({ page }) => {
      await page.clock.install();
      const { item, state } = await newsFixtures(page);
      await page.goto(device.hasTouch ? './#news=101' : './#/news/101');
      await page.getByLabel('访问密码').fill('finevent-e2e-password-only');
      await page.getByRole('button', { name: '进入工作台' }).click();
      await expect(page.getByRole('heading', { name: item.title, exact: true })).toBeFocused();
      await expect(page.locator('.news-evidence')).toContainText('货币与流动性');
      const update = page.getByRole('button', { name: '更新当前内容' });
      await expect(update).toHaveCount(0);

      state.changed = true;
      item.content = '修订后的报道正文';
      await page.clock.fastForward(31000);
      await expect(update).toBeVisible();
      await expect(page.locator('.news-body > .news-copy')).not.toHaveText(item.content);
      await device.activate(update);
      await expect(page.locator('.news-body > .news-copy')).toHaveText(item.content);
      await expect(update).toHaveCount(0);
      const nextCheck = page.waitForResponse('**/api/v1/news/changes?after=2');
      await page.clock.fastForward(31000);
      await nextCheck;
      await expect(update).toHaveCount(0);
    });
  });
}

for (const viewport of [
  { width: 1366, height: 900 },
  { width: 390, height: 844 },
]) {
  test(`报道：${viewport.width}px 高级搜索、分页阅读与刷新返回`, async ({ page }, testInfo) => {
    const { state } = await newsFixtures(page, 18);
    await page.setViewportSize(viewport);
    await loginNews(page);
    const advanced = page.getByRole('button', { name: '高级搜索', exact: true });
    const search = page.getByRole('searchbox', { name: '标题或正文' });
    await search.fill('报道');
    await search.dispatchEvent('keydown', { key: 'Enter', isComposing: true });
    await advanced.click();
    await expect(page.getByRole('combobox')).toHaveCount(6);
    expect(state.queries.some((query) => query.get('q') === '报道')).toBe(false);
    await page.getByRole('combobox', { name: '来源', exact: true }).click();
    await page.getByRole('option', { name: '华尔街见闻快讯', exact: true }).click();
    await page.getByRole('button', { name: '应用筛选', exact: true }).click();
    await expect.poll(() => state.queries.at(-1)?.get('source')).toBe('wallstreetcn');
    expect(state.queries.at(-1)?.get('q')).toBe('报道');
    const chip = page.getByRole('button', { name: '移除来源条件：华尔街见闻快讯' });
    await expect(chip).toBeVisible();
    await advanced.click();
    await page.getByRole('button', { name: '重置条件' }).click();
    await page.getByRole('button', { name: '取消', exact: true }).click();
    await expect(chip).toBeVisible();
    await page.getByRole('button', { name: '加载更多报道' }).click();
    const row = page.getByRole('button', { name: '分页报道 27', exact: true });
    await row.evaluate((el) => el.scrollIntoView({ block: 'center' }));
    const top = await row.evaluate((el) => el.getBoundingClientRect().top);
    expect(await page.evaluate(() => window.scrollY)).toBeGreaterThan(100);
    await row.press('Enter');
    await expect(page.getByRole('heading', { name: '分页报道 27' })).toBeFocused();
    await page.getByRole('button', { name: '返回报道列表' }).click();
    await expect(row).toBeFocused();
    expect(
      Math.abs((await row.evaluate((el) => el.getBoundingClientRect().top)) - top),
    ).toBeLessThanOrEqual(1);
    state.changed = true; // New reports move the anchor to the last row of the third page.
    await page.goForward();
    await page.reload();
    await expect(page.getByRole('heading', { name: '分页报道 27' })).toBeFocused();
    await page.goBack();
    await expect(row).toBeFocused();
    await expect(row).toContainText('刚读到这里');
    expect(
      Math.abs((await row.evaluate((el) => el.getBoundingClientRect().top)) - top),
    ).toBeLessThanOrEqual(1);
    await expect(search).toHaveValue('报道');
    await expect(chip).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath('reading-position.png') });
    await chip.click();
    await expect.poll(() => state.queries.at(-1)?.get('source')).toBe('');
  });
}
