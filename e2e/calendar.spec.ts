import { createHash } from 'node:crypto';

import { expect, test, type Page } from '@playwright/test';

async function login(page: Page) {
  await page.goto('./#/calendar');
  await page.getByLabel('访问密码').fill('finevent-e2e-password-only');
  await page.getByRole('button', { name: '进入工作台' }).click();
  await expect(page.getByRole('heading', { name: '财经日历', exact: true })).toBeVisible();
}

for (const width of [1280, 393]) {
  test(`零售销售详情保留来源和时间精度（${width}px）`, async ({ page }) => {
    await page.setViewportSize({ width, height: 852 });
    const annual = {
      id: 'annual-retail',
      source: 'omb',
      source_id: 'advance-retail:2026-12',
      title: '美国零售销售（初值）',
      kind: 'macro',
      status: 'scheduled',
      importance: 3,
      conditions: '',
      fund_code: '',
      evidence: 'OMB 年表公布的日期',
      updated_at: 1,
      timezone: 'America/New_York',
      start: '2027-01-15',
      end: '2027-01-15',
      precision: 'date',
      url: 'https://www.whitehouse.gov/wp-content/uploads/2026/10/FY2027-PFEI.pdf',
      version: 1,
    };
    const recent = {
      ...annual,
      id: 'recent-retail',
      source: 'nyfed',
      source_id: 'advance-retail:2026-10',
      start: '2026-11-17T08:30:00-05:00',
      end: '2026-11-17T08:30:00-05:00',
      precision: 'minute',
      url: 'https://www.newyorkfed.org/research/calendars/i-nov26.html',
      provenance: {
        mode: 'supplement',
        authority_url: 'https://www.census.gov/retail/marts/www/marts_current.pdf',
      },
    };
    for (const event of [annual, recent]) {
      await page.route(`**/api/v1/calendar/${event.id}`, (route) =>
        route.fulfill({ json: { event, revisions: [] } }),
      );
    }
    await login(page);
    await page.goto('./#/calendar/events/annual-retail');
    await expect(page.getByText('2027-01-15 · 仅日期', { exact: true })).toBeVisible();
    await expect(page.getByRole('link', { name: '查看原文 ↗' })).toHaveAttribute(
      'href',
      annual.url,
    );
    await page.goto('./#/calendar/events/recent-retail');
    await expect(page.getByText(/21:30 · 北京时间/)).toBeVisible();
    const authority = page.getByRole('link', { name: '查看 Census 零售销售报告 ↗' });
    await authority.focus();
    await expect(authority).toBeFocused();
    await expect(authority).toHaveAttribute('href', recent.provenance.authority_url);
    await page.keyboard.press('Tab');
    await expect(page.getByRole('link', { name: '查看原文 ↗' })).toBeFocused();
  });
}

test('基金：草稿持久化、人工确认、区间修改与取消', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 393, height: 852 });
  await login(page);
  await page.getByRole('button', { name: '基金录入', exact: true }).click();
  await page.getByLabel('公告原文链接').fill('https://example.org/fund-announcement');
  await page
    .getByLabel('公告文字')
    .fill(
      '基金代码：123456。开放赎回时间为2026年11月2日至2026年11月6日。仅限交易日，每日限额10000元。',
    );
  await page.getByRole('button', { name: '提取草稿' }).click();
  await expect(page.getByLabel('基金代码', { exact: true })).toHaveValue('123456');
  await expect(page.getByLabel('开始日期')).toHaveValue('2026-11-02');
  await expect(page.getByRole('button', { name: '确认入历' })).toBeDisabled();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
  await page.screenshot({ path: testInfo.outputPath('calendar-mobile-fund.png'), fullPage: true });
  await page.reload();
  await expect(page.getByLabel('基金代码', { exact: true })).toHaveValue('123456');
  await page.getByLabel('事件名称').fill('测试基金赎回窗口');
  await page.getByRole('checkbox').check();
  await page.getByRole('checkbox').press('Tab');
  await expect(page.getByRole('button', { name: '确认入历' })).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('heading', { name: '测试基金赎回窗口', exact: true })).toBeFocused();
  await expect(page.getByRole('heading', { name: '测试基金赎回窗口' })).toBeVisible();
  await page.getByRole('button', { name: '修改日期、条件或状态' }).click();
  await page.getByLabel('开始日期').fill('2026-11-03');
  await page.getByRole('button', { name: '保存事件' }).click();
  await expect(page.getByText('基金公告 · 已改期 · 第 2 版')).toBeVisible();
  await page.getByRole('button', { name: '修改日期、条件或状态' }).click();
  await page.getByLabel('确定程度').selectOption('cancelled');
  await page.getByRole('button', { name: '保存事件' }).click();
  await expect(page.getByText('基金公告 · 已取消 · 第 3 版')).toBeVisible();
  await page.getByText('修订记录 · 3 版', { exact: true }).click();
  await expect(page.getByText('第 1 版 · 已确认', { exact: true })).toBeVisible();
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.screenshot({ path: testInfo.outputPath('calendar-fund-detail.png'), fullPage: true });
});

test('日历：补录时刻、月份切换、日期选择、窄屏议程', async ({ page }, testInfo) => {
  await login(page);
  await page.getByRole('button', { name: '补录事件' }).click();
  await expect(page.getByRole('heading', { name: '补录官方事件' })).toBeFocused();
  await page.getByLabel('事件名称').fill('测试宏观发布');
  await page.getByLabel('时间精度').selectOption('minute');
  await page.getByLabel('开始时间').fill('2026-11-05T09:30');
  await page.getByLabel('结束时间').fill('2026-11-05T09:30');
  await page.getByLabel('官方原文链接').fill('https://example.org/official');
  await page.getByLabel('原文依据').fill('测试用官方公告明确北京时间11月5日9时30分发布。');
  await page.getByRole('button', { name: '保存事件' }).click();
  await expect(page.locator('.event-datetime')).toHaveText('2026/11/05 09:30 · 北京时间');
  await page.getByRole('button', { name: '返回财经日历', exact: true }).click();
  await page.getByRole('button', { name: '2026-11-05，', exact: false }).click();
  await expect(page.getByRole('button', { name: /测试宏观发布/ })).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath('calendar-desktop-month.png'),
    fullPage: true,
  });
  await page.getByRole('button', { name: '下个月' }).click();
  await expect(page.getByRole('heading', { name: '2026 年 12 月' })).toBeVisible();
  await page.getByRole('button', { name: '上个月' }).click();
  await page.getByRole('button', { name: '议程', exact: true }).click();
  await page.getByRole('link', { name: '设置', exact: true }).click();
  await page.getByRole('link', { name: '日历官方来源', exact: true }).click();
  await page.getByText('官方来源 · 8 项待核查', { exact: true }).click();
  await expect(page.getByRole('link', { name: '白宫管理和预算办公室' })).toHaveAttribute(
    'href',
    'https://www.whitehouse.gov/omb/information-resources/guidance/us-principal-federal-economic-indicators/',
  );
  await expect(page.getByRole('link', { name: '美国人口普查局' })).toHaveAttribute(
    'href',
    'https://www.census.gov/retail/release_schedule.html',
  );
  await expect(page.getByRole('link', { name: '美国劳工部' })).toHaveAttribute(
    'href',
    'https://oui.doleta.gov/unemploy/claims_arch.asp',
  );
  await expect(page.getByText('当前服务未启用自动同步。')).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('calendar-desktop.png'), fullPage: true });
  await page.getByRole('link', { name: '财经日历', exact: true }).click();
  await page.setViewportSize({ width: 393, height: 852 });
  await expect(page.getByRole('button', { name: /测试宏观发布/ })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
  await page.getByRole('button', { name: '月历', exact: true }).click();
  await page.getByRole('button', { name: '2026-11-05，', exact: false }).click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
  await page.screenshot({ path: testInfo.outputPath('calendar-mobile.png'), fullPage: true });
  await page.getByRole('button', { name: /测试宏观发布/ }).click();
  await expect(page.getByRole('heading', { name: '测试宏观发布', exact: true })).toBeFocused();
});

test('来源失败和日历加载失败提供可恢复状态', async ({ page }) => {
  await page.route('**/api/v1/calendar?*', (route) =>
    route.fulfill({ status: 503, json: { error: '暂时不可用' } }),
  );
  await login(page);
  await expect(page.getByRole('alert')).toHaveText('暂时不可用');
  await page.route('**/api/v1/calendar?*', (route) =>
    route.fulfill({
      json: {
        events: [],
        sync_enabled: true,
        sources: [
          {
            id: 'bls',
            name: '美国 BLS',
            url: 'https://www.bls.gov/schedule/news_release/bls.ics',
            last_success: 0,
            last_attempt: 1,
            error: '官方来源返回 HTTP 403',
            count: 0,
            coverage_end: '',
          },
        ],
      },
    }),
  );
  await page.getByRole('button', { name: '重试', exact: true }).click();
  await page.getByRole('link', { name: '设置', exact: true }).click();
  await page.getByRole('link', { name: '日历官方来源', exact: true }).click();
  await page.getByText('官方来源 · 1 项待核查', { exact: true }).click();
  await expect(page.getByText('官方来源返回 HTTP 403')).toBeVisible();
});

test('官方快照：主源未恢复、核对时间、原文下载及窄屏键盘访问', async ({ page }, testInfo) => {
  // The official fixture is November 2026; month navigation must not depend on today's date.
  await page.clock.setFixedTime(new Date('2026-10-31T04:00:00Z'));
  await login(page);
  await expect(page.getByRole('status')).toContainText('58 项日程来自已核对的官方快照');
  const response = await page.request.get('api/v1/calendar?from=2026-11-01&to=2026-11-30');
  const data = await response.json();
  const source = data.sources.find((item: { id: string }) => item.id === 'nbs');
  const snapshotEvent = data.events.find((item: { source: string }) => item.source === 'nbs');
  expect(source.last_success).toBe(0);
  await page.getByRole('link', { name: '设置', exact: true }).click();
  await page.getByRole('link', { name: '日历官方来源', exact: true }).click();
  const summary = page.locator('summary').filter({ hasText: '官方来源' });
  await summary.focus();
  await page.keyboard.press('Enter');
  await expect(page.getByText('官方快照 · 47 项尚未由主源更新')).toBeVisible();
  const link = page.getByRole('link', { name: '下载核对时原文' });
  await link.focus();
  const downloadPromise = page.waitForEvent('download');
  await page.keyboard.press('Enter');
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe('official-calendar-snapshot.txt');
  const original = await page.request.get((await link.getAttribute('href')) ?? '');
  expect(
    createHash('sha256')
      .update(await original.body())
      .digest('hex'),
  ).toBe(source.snapshots[0].sha256);
  await page.screenshot({
    path: testInfo.outputPath('calendar-snapshot-desktop.png'),
    fullPage: true,
  });
  await page.getByRole('link', { name: '财经日历', exact: true }).click();
  await page.setViewportSize({ width: 393, height: 852 });
  await page.getByRole('button', { name: '下个月' }).click();
  await expect(page.getByRole('heading', { name: '2026 年 11 月', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '议程', exact: true }).click();
  await page
    .getByRole('button', { name: new RegExp(snapshotEvent.title) })
    .first()
    .click();
  await expect(page.getByRole('heading', { name: snapshotEvent.title, exact: true })).toBeFocused();
  await expect(page.getByText('官方原文快照', { exact: true })).toBeVisible();
  await expect(page.getByText(/核对 2026\/10\/30/)).toBeVisible();
  await expect(page.getByText('此后改期尚未自动核对，请以官方最新公告为准。')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({
    path: testInfo.outputPath('calendar-snapshot-mobile.png'),
    fullPage: true,
  });
  await page.getByRole('button', { name: '返回财经日历', exact: true }).click();
  await expect(
    page.getByRole('button', { name: new RegExp(snapshotEvent.title) }).first(),
  ).toBeFocused();
});

for (const width of [1280, 393]) {
  test(`BLS 年度修订：${width}px 核对原表、独立事件、北京时间和关注`, async ({ page }) => {
    await page.setViewportSize({ width, height: 852 });
    await page.clock.setFixedTime(new Date('2027-08-01T04:00:00Z'));
    await login(page);
    await page.getByRole('link', { name: '设置', exact: true }).click();
    await page.getByRole('link', { name: '日历官方来源', exact: true }).click();
    await page.locator('summary').filter({ hasText: '官方来源' }).press('Enter');
    const source = page
      .locator('li')
      .filter({ has: page.getByRole('link', { name: '美国 BLS', exact: true }) });
    await expect(source.getByText('尚未成功同步')).toBeVisible();
    const year = source.locator('.snapshot-record').filter({ hasText: /2027 年\s*BLS 年表/ });
    await expect(year.getByRole('link', { name: '查看官方发布页' })).toHaveAttribute(
      'href',
      'https://www.bls.gov/schedule/2027/home.htm',
    );
    await page.getByRole('link', { name: '财经日历', exact: true }).click();
    await page.getByRole('button', { name: '议程', exact: true }).click();
    const national = page.getByRole('button', { name: /非农就业年度基准初步修订（全国）/ });
    await expect(national).toHaveCount(1);
    await expect(page.getByRole('button', { name: /年度基准初步修订（州及地区）/ })).toHaveCount(1);
    await national.press('Enter');
    await expect(
      page.getByRole('heading', { name: /非农就业年度基准初步修订（全国）/ }),
    ).toBeFocused();
    await expect(page.locator('.event-datetime')).toHaveText('2027/08/26 22:00 · 北京时间');
    await expect(page.getByText('官方日程摘录', { exact: true })).toBeVisible();
    await expect(page.getByRole('link', { name: '下载核对时日程摘录' })).toBeVisible();
    const watch = page.getByRole('button', { name: /^(取消)?关注此事件$/ });
    const pressed = await watch.getAttribute('aria-pressed');
    await watch.press('Enter');
    await expect(watch).toHaveAttribute('aria-pressed', pressed === 'true' ? 'false' : 'true');
    await page.getByRole('button', { name: '返回财经日历', exact: true }).click();
    await expect(national).toBeFocused();
  });
}

test('补充来源：单独标记、来源链接和 BLS 故障同时保留', async ({ page }, testInfo) => {
  await page.clock.setFixedTime(new Date('2026-10-31T04:00:00Z'));
  const event = {
    id: 'supplement',
    source: 'nyfed',
    source_id: '2026-11:empsit',
    title: '美国就业报告（非农）',
    kind: 'macro',
    start: '2026-11-06T08:30:00-05:00',
    end: '2026-11-06T08:30:00-05:00',
    timezone: 'America/New_York',
    precision: 'minute',
    status: 'scheduled',
    importance: 3,
    url: 'https://www.newyorkfed.org/research/calendars/i-nov26.html',
    evidence: '纽约联储官方日历：2026年11月6日08:30（美国东部时间）。',
    conditions: '仅补充关键 BLS 事件。',
    fund_code: '',
    version: 1,
    updated_at: 1,
    provenance: {
      mode: 'supplement',
      authority_url: 'https://www.bls.gov/news.release/empsit.toc.htm',
    },
  };
  await page.route('**/api/v1/calendar/supplement', (route) =>
    route.fulfill({ json: { event, revisions: [{ event, saved_at: 1 }] } }),
  );
  await page.route('**/api/v1/calendar?*', (route) =>
    route.fulfill({
      json: {
        events: [event],
        sync_enabled: true,
        sources: [
          {
            id: 'bls',
            name: '美国 BLS',
            url: 'https://www.bls.gov/schedule/news_release/bls.ics',
            last_success: 0,
            last_attempt: 1,
            error: '官方来源返回 HTTP 403',
            count: 0,
            coverage_end: '',
          },
        ],
      },
    }),
  );
  await login(page);
  await page.getByRole('link', { name: '设置', exact: true }).click();
  await page.getByRole('link', { name: '日历官方来源', exact: true }).click();
  await page.locator('summary').filter({ hasText: '官方来源' }).click();
  await expect(page.getByText('官方来源返回 HTTP 403')).toBeVisible();
  await page.getByRole('link', { name: '财经日历', exact: true }).click();
  await page.getByRole('button', { name: '下个月' }).click();
  await expect(page.getByRole('heading', { name: '2026 年 11 月', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '议程', exact: true }).click();
  await page.getByRole('button', { name: /美国就业报告/ }).click();
  await expect(page.getByText('纽约联储 · 补充来源 · 预定 · 第 1 版')).toBeVisible();
  await expect(
    page.getByText('日程转引自纽约联储，覆盖不完整，不表示 BLS 主源恢复。'),
  ).toBeVisible();
  await expect(page.getByRole('link', { name: '查看 BLS 指标页面 ↗' })).toHaveAttribute(
    'href',
    event.provenance.authority_url,
  );
  await expect(page.getByRole('link', { name: '查看原文 ↗', exact: true })).toHaveAttribute(
    'href',
    event.url,
  );
  await page.screenshot({
    path: testInfo.outputPath('calendar-supplement-desktop.png'),
    fullPage: true,
  });
  await page.setViewportSize({ width: 393, height: 852 });
  const link = page.getByRole('link', { name: '查看 BLS 指标页面 ↗' });
  await link.focus();
  await expect(link).toBeFocused();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({
    path: testInfo.outputPath('calendar-supplement-mobile.png'),
    fullPage: true,
  });
});
