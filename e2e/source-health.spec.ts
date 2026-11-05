import { expect, test, type Page } from '@playwright/test';

import type { TrendHealth, TrendFailurePage } from '../apps/web/src/source-health.js';

async function sourceFixtures(page: Page) {
  const now = Math.floor(Date.now() / 1000);
  const base = {
    last_attempt: now - 60,
    last_status: 'success',
    last_success: now - 60,
    last_failure: 0,
    recovered_at: 0,
    stale: false,
  };
  const health: TrendHealth = {
    server_time: now,
    collection_enabled: true,
    trendradar_enabled: true,
    read_attempt: now,
    read_success: now,
    read_error: '',
    read_stale: false,
    warnings: [],
    latest_cycle: now - 60,
    history_since: now - 86400,
    sources: [
      {
        ...base,
        id: 'cls-telegraph',
        state: 'healthy',
        last_failure: now - 1860,
        recovered_at: now - 60,
      },
      {
        ...base,
        id: 'wallstreetcn-quick',
        state: 'failed',
        last_status: 'failed',
        last_failure: now - 60,
        last_success: now - 3660,
      },
      { ...base, id: 'v2ex', state: 'missing', last_attempt: now - 1860, last_success: now - 1860 },
      {
        ...base,
        id: 'zaobao',
        state: 'stale',
        last_attempt: now - 5000,
        last_success: now - 5000,
        stale: true,
      },
      { ...base, id: 'juejin', state: 'unknown', last_status: 'unknown', last_success: now - 1860 },
    ],
  };
  const failure = {
    id: 101,
    source: 'wallstreetcn-quick',
    at: now - 3660,
    status: 'failed',
    recovered_at: now - 1860,
    corrected: false,
  };
  const state = {
    fail: false,
    failHistory: false,
    emptyHistory: false,
    requests: 0,
    historyQueries: [] as URLSearchParams[],
  };
  await page.route('**/api/v1/news/source-health', (route) => {
    state.requests++;
    return state.fail
      ? route.fulfill({ status: 503, json: { error: '测试：状态读取暂时失败' } })
      : route.fulfill({ json: health });
  });
  await page.route('**/api/v1/news/source-failures?**', (route) => {
    const params = new URL(route.request().url()).searchParams;
    state.historyQueries.push(params);
    if (state.failHistory)
      return route.fulfill({ status: 503, json: { error: '测试：故障历史暂时失败' } });
    const result: TrendFailurePage = {
      items: state.emptyHistory
        ? []
        : [
            {
              ...failure,
              id: params.has('before') ? 100 : 101,
              source: params.get('source') || failure.source,
              corrected: params.has('before'),
            },
          ],
      next: state.emptyHistory || params.has('before') ? '' : `${now - 3660}:101`,
    };
    return route.fulfill({ json: result });
  });
  return { health, state };
}

async function loginSources(page: Page) {
  await page.goto('./');
  await page.getByLabel('访问密码').fill('finevent-e2e-password-only');
  await page.getByRole('button', { name: '进入工作台' }).click();
  await page.getByRole('button', { name: '验证工作台', exact: true }).click();
  await page.getByRole('button', { name: '02 采集时效' }).click();
  await expect(page.getByRole('heading', { name: 'TrendRadar 来源健康' })).toBeVisible();
}

test('来源健康：状态分层、恢复历史、筛选分页、键盘与窄屏', async ({ page }, testInfo) => {
  await page.clock.install();
  const { state } = await sourceFixtures(page);
  await page.setViewportSize({ width: 1280, height: 900 });
  await loginSources(page);
  const panel = page.getByRole('region', { name: 'TrendRadar 来源健康' });
  await expect(panel.getByText('5 个已观察来源 · 4 项需核查')).toBeVisible();
  for (const text of [
    '最近采集成功',
    '最近采集失败',
    '最新一轮缺记录',
    '采集记录已过期',
    '状态待核查',
  ]) {
    await expect(panel.getByText(text, { exact: true })).toBeVisible();
  }
  const summary = panel.locator('summary').filter({ hasText: '财联社电报' });
  await summary.focus();
  await page.keyboard.press('Enter');
  const row = panel.locator('details.source-health-row').filter({ hasText: '财联社电报' });
  await expect(row.getByText('该次失败后首次成功', { exact: true })).toBeVisible();
  await page.keyboard.press('Tab');
  const open = row.getByRole('button', { name: '查看财联社电报故障记录' });
  await expect(open).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(panel.getByRole('heading', { name: '采集故障历史' })).toBeFocused();
  const filter = panel.getByLabel('故障来源');
  await expect(filter).toHaveValue('cls-telegraph');
  await filter.selectOption('wallstreetcn-quick');
  await panel.getByRole('button', { name: '查询故障记录' }).click();
  await expect.poll(() => state.historyQueries.at(-1)?.get('source')).toBe('wallstreetcn-quick');
  await panel.getByRole('button', { name: '加载更早故障' }).click();
  await expect(panel.locator('.source-failure-list li')).toHaveCount(2);
  await expect(panel.getByText('上游已修订或移除此轮状态', { exact: false })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('source-health-desktop.png'), fullPage: true });
  await filter.focus();
  const previous = state.requests;
  await page.clock.runFor(31000);
  await expect.poll(() => state.requests).toBeGreaterThan(previous);
  await expect(filter).toBeFocused();
  await expect(filter).toHaveValue('wallstreetcn-quick');
  await expect(panel.locator('.source-failure-list li')).toHaveCount(2);
  await page.setViewportSize({ width: 393, height: 852 });
  await panel.locator('summary').filter({ hasText: '联合早报' }).click();
  await expect(panel.getByText('超过 40 分钟没有新的来源记录。')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)).toBe(
    false,
  );
  await page.screenshot({ path: testInfo.outputPath('source-health-mobile.png'), fullPage: true });
  state.emptyHistory = true;
  await filter.selectOption('v2ex');
  await panel.getByRole('button', { name: '查询故障记录' }).click();
  await expect(
    panel.getByText('当前保留范围内未见该筛选的失败记录', { exact: false }),
  ).toBeVisible();
  state.failHistory = true;
  await panel.getByRole('button', { name: '查询故障记录' }).click();
  await expect(panel.getByRole('alert')).toContainText('故障历史暂时失败');
  state.failHistory = false;
  await panel.getByRole('button', { name: '查询故障记录' }).click();
  await expect(panel.getByRole('alert')).toHaveCount(0);
});

test('来源健康：加载失败重试、后台保留错误、缺数据与新闻入口', async ({ page }, testInfo) => {
  await page.clock.install();
  const { health, state } = await sourceFixtures(page);
  state.fail = true;
  await loginSources(page);
  const panel = page.getByRole('region', { name: 'TrendRadar 来源健康' });
  await expect(panel.getByRole('alert')).toContainText('状态读取暂时失败');
  state.fail = false;
  await panel.getByRole('button', { name: '刷新来源状态' }).click();
  await expect(panel.getByText('5 个已观察来源 · 4 项需核查')).toBeVisible();
  state.fail = true;
  await panel.getByRole('button', { name: '刷新来源状态' }).click();
  await expect(panel.getByRole('alert')).toContainText('以下保留上次读取结果');
  state.fail = false;
  const previous = state.requests;
  await page.clock.runFor(31000);
  await expect.poll(() => state.requests).toBeGreaterThan(previous);
  await expect(panel.getByRole('alert')).toContainText('状态读取暂时失败');
  health.read_error = '测试：状态表缺失';
  health.read_stale = true;
  health.sources = [];
  health.latest_cycle = 0;
  health.history_since = 0;
  health.read_success = 0;
  health.trendradar_enabled = false;
  health.warnings = ['测试：前一日数据库尚未读到，该日记录未补齐。'];
  await panel.getByRole('button', { name: '刷新来源状态' }).click();
  await expect(panel.getByText('尚无来源状态记录', { exact: true })).toBeVisible();
  await expect(panel.getByText('来源状态采样未启用', { exact: false })).toBeVisible();
  await expect(panel.getByText('测试：状态表缺失', { exact: false })).toBeVisible();
  await page.getByRole('button', { name: '新闻事件流', exact: true }).click();
  await page.getByText('采集通道状态', { exact: true }).click();
  await expect(page.getByRole('heading', { name: 'TrendRadar 来源健康' })).toBeVisible();
  await expect(page.getByText('数据库导入成功不代表新闻源正常', { exact: false })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('source-health-empty.png'), fullPage: true });
});
