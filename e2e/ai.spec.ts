import { expect, test, type Page } from '@playwright/test';

import type { AIJob, AIOverview } from '../apps/web/src/ai.js';
import type { NewsEntry } from '../apps/web/src/news.js';

// Explicit UI fixtures: paid network requests and persistence are exercised separately by Go tests.
async function fixtures(page: Page) {
  const now = Math.floor(Date.now() / 1000);
  const item: NewsEntry = {
    id: 51,
    source: 'wallstreetcn',
    source_id: 'ui-ai-test',
    title: '测试报道：央行宣布降准',
    content: '测试资料：央行宣布降准0.5个百分点，实施细则待公布。<img src=x onerror=alert(1)>',
    url: 'https://example.org/ai-fixture',
    published_at: now - 80,
    first_seen: now - 20,
    last_seen: now,
    ingested_at: now - 20,
    updated_at: now - 20,
    source_observed_at: 0,
    version: 1,
    baseline: false,
    classification: {
      categories: ['monetary'],
      industries: ['finance'],
      importance: 3,
      reasons: ['测试规则命中'],
      rule_version: 'topics-v1',
    },
  };
  const completed: AIJob = {
    id: 'test-analysis',
    model: 'deepseek-flash',
    prompt_version: 'impact-v1',
    status: 'succeeded',
    created: now,
    completed: now + 5,
    expires: now + 365 * 86400,
    error: '',
    attempts: 1,
    stale: false,
    evidence: [
      {
        id: 'e1',
        news_id: 51,
        version: 1,
        source: item.source,
        url: item.url,
        title: item.title,
        text: item.content,
        published_at: item.published_at,
        truncated: false,
      },
    ],
    result: {
      facts: [{ text: '测试资料描述降准', evidence: 'e1', quote: '央行宣布降准0.5个百分点' }],
      categories: ['monetary'],
      impacts: [
        {
          target: '银行',
          market: 'A股',
          industry: '金融',
          direction: 'mixed',
          path: '测试：流动性与息差存在不同作用',
          conditions: ['实施细则落地'],
          horizon: '短期至中期',
          counterfactors: ['需求偏弱'],
          evidence: ['e1'],
        },
      ],
      metrics: [
        {
          name: '降准幅度',
          actual: { value: '0.5个百分点', evidence: 'e1', quote: '央行宣布降准0.5个百分点' },
          expected: null,
          previous: null,
        },
      ],
      uncertainty: '实施细则待公布',
    },
    calls: [
      {
        started: now,
        status: 'succeeded',
        reserved_nano: 40000000,
        estimated_nano: 1000000,
        prompt_tokens: 100,
        completion_tokens: 200,
        price_version: 'ui-fixture',
        price_basis: 'off_peak_estimate',
      },
    ],
  };
  const info: AIOverview = {
    settings: { enabled: false, automatic: false },
    configured: true,
    model: 'deepseek-flash',
    price_version: 'ui-fixture',
    prices_valid_until: '2026-12-01',
    month: '2026-11',
    budget_nano: 30000000000,
    estimated_nano: 100000000,
    reserved_nano: 20000000,
    probe_nano: 20000000,
    remaining_nano: 29880000000,
    queued: 0,
    not_before: 0,
    recent: [],
  };
  const state = {
    info,
    job: null as AIJob | null,
    posts: [] as { retry: boolean }[],
    failSave: false,
    failRead: false,
  };
  await page.route('**/api/v1/ai**', async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith('/settings')) {
      if (state.failSave)
        return route.fulfill({ status: 503, json: { error: '测试：保存暂时失败' } });
      state.info.settings = route.request().postDataJSON();
      return route.fulfill({ json: state.info.settings });
    }
    if (url.pathname.includes('/analyses/')) return route.fulfill({ json: state.job ?? completed });
    if (state.failRead)
      return route.fulfill({ status: 503, json: { error: '测试：用量暂时无法读取' } });
    return route.fulfill({ json: state.info });
  });
  await page.route('**/api/v1/news**', async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.split('/').pop();
    if (path === 'calendar-links') return route.fulfill({ json: { items: [], next: 0 } });
    if (path === 'analysis') {
      if (route.request().method() === 'POST') {
        state.posts.push(route.request().postDataJSON());
        state.job = { ...completed, status: 'queued', result: null, calls: [], completed: 0 };
        return route.fulfill({ status: 202, json: { id: completed.id } });
      }
      return route.fulfill({ json: { job: state.job } });
    }
    if (path === 'meta')
      return route.fulfill({
        json: {
          categories: [{ id: 'monetary', name: '货币与流动性' }],
          industries: [],
          sources: [item.source],
          rule_version: 'topics-v1',
          collection_enabled: true,
          trendradar_enabled: true,
          server_time: now,
          channels: [],
        },
      });
    if (path === 'changes')
      return route.fulfill({ json: { watermark: 1, changed: 0, reset: false } });
    if (path === 'news')
      return route.fulfill({ json: { items: [item], next: '', watermark: 1, backlog: 0 } });
    return route.fulfill({
      json: {
        item,
        revisions: [],
        related: [],
        revision_more: false,
        relation_scope: '测试：只有当前来源',
      },
    });
  });
  return { state, completed, item };
}
async function login(page: Page, hash = 'ai') {
  await page.goto(`./#${hash}`);
  await page.getByLabel('访问密码').fill('finevent-e2e-password-only');
  await page.getByRole('button', { name: '进入工作台' }).click();
}

test('AI：开关、预算、错误和刷新不覆盖未保存设置，桌面与窄屏', async ({ page }, testInfo) => {
  const { state } = await fixtures(page);
  await page.clock.install();
  await login(page);
  await expect(page.getByRole('heading', { name: 'AI 解读', exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: '本月可用 ¥29.8800' })).toBeVisible();
  const enabled = page.getByLabel('启用 AI 解读');
  await enabled.focus();
  await page.keyboard.press('Space');
  await page.getByLabel('自动解读新的重要报道').check();
  await page.clock.fastForward(31000);
  await expect(enabled).toBeChecked();
  state.failSave = true;
  await page.getByRole('button', { name: '保存解读设置' }).click();
  await expect(page.getByRole('alert')).toContainText('保存暂时失败');
  await page.clock.fastForward(31000);
  await expect(page.getByRole('alert')).toContainText('保存暂时失败');
  state.failSave = false;
  await page.getByRole('button', { name: '保存解读设置' }).click();
  await expect(page.getByRole('status')).toContainText('解读设置已保存');
  expect(state.info.settings).toEqual({ enabled: true, automatic: true });
  await page.screenshot({ path: testInfo.outputPath('ai-settings-desktop.png'), fullPage: true });
  await page.reload();
  await expect(enabled).toBeChecked();
  await page.setViewportSize({ width: 393, height: 852 });
  await enabled.uncheck();
  await page.getByRole('button', { name: '保存解读设置' }).click();
  await expect(page.getByRole('status')).toContainText('解读设置已保存');
  expect(state.info.settings.enabled).toBe(false);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath('ai-settings-mobile.png'), fullPage: true });
  state.info.configured = false;
  state.failRead = true;
  await page.getByRole('button', { name: '刷新用量' }).click();
  await expect(page.getByRole('alert')).toContainText('用量暂时无法读取');
  state.failRead = false;
  await page.getByRole('button', { name: '刷新用量' }).click();
  await expect(enabled).toBeDisabled();
  await expect(page.getByRole('alert')).toHaveCount(0);
});

test('AI：手动排队、引用快照、缺失数值、修订和有意重试', async ({ page }, testInfo) => {
  const { state, completed, item } = await fixtures(page);
  state.info.settings.enabled = true;
  await page.clock.install();
  await login(page, 'news=51');
  await expect(page.getByRole('heading', { name: item.title, exact: true })).toBeFocused();
  await expect(page.getByText('这条报道尚无解读。可根据来源请求一次分析。')).toBeVisible();
  const request = page.getByRole('button', { name: '请求事件解读' });
  await request.focus();
  await page.keyboard.press('Enter');
  await expect(page.getByText(/等待解读 · deepseek-flash/)).toBeVisible();
  expect(state.posts).toEqual([{ retry: false }]);
  state.job = completed;
  await page.getByRole('button', { name: '刷新解读' }).click();
  await expect(page.getByRole('heading', { name: '潜在影响 · 模型推断' })).toBeVisible();
  await expect(page.getByText('来源未提供', { exact: true })).toHaveCount(2);
  const snapshot = page.getByText('引用快照 · 1 份', { exact: true });
  await snapshot.focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('link', { name: '查看引用原文 ↗' })).toHaveAttribute(
    'href',
    item.url,
  );
  await expect(page.locator('.ai-snapshots img')).toHaveCount(0);
  await page.getByText('调用与费用记录', { exact: true }).click();
  await expect(page.getByText('用量估算 ¥0.0010')).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('ai-result-desktop.png'), fullPage: true });
  state.job = { ...completed, stale: true };
  await page.getByRole('button', { name: '刷新解读' }).click();
  await expect(page.getByText(/来源已修订。这份解读基于旧版本/)).toBeVisible();
  await page.getByRole('button', { name: '解读最新版本' }).click();
  expect(state.posts.at(-1)).toEqual({ retry: false });
  state.job = {
    ...completed,
    status: 'uncertain',
    result: null,
    error: '测试：上次调用中断',
    attempts: 1,
  };
  await page.getByRole('button', { name: '刷新解读' }).click();
  await expect(page.getByText(/重试可能产生新费用/)).toBeVisible();
  await page.clock.fastForward(31000);
  expect(state.posts).toHaveLength(2);
  await page.getByRole('button', { name: '重试解读', exact: true }).click();
  expect(state.posts.at(-1)).toEqual({ retry: true });
  state.job = { ...completed, stale: true };
  await page.getByRole('button', { name: '刷新解读' }).click();
  await page.setViewportSize({ width: 393, height: 852 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath('ai-result-mobile.png'), fullPage: true });
  state.info.recent = [
    {
      id: completed.id,
      status: 'succeeded',
      created: completed.created,
      error: '',
      attempts: 1,
      title: item.title,
    },
  ];
  await page.getByRole('button', { name: 'AI 解读', exact: true }).click();
  await page.getByRole('button', { name: item.title, exact: true }).click();
  await expect(page.locator('#ai-history-heading')).toBeFocused();
  await expect(page.getByRole('heading', { name: '潜在影响 · 模型推断' })).toBeVisible();
});

test('AI：预算等待和无效输出不显示为成功解读', async ({ page }) => {
  const { state, completed } = await fixtures(page);
  state.info.settings.enabled = true;
  state.job = { ...completed, status: 'budget_wait', result: null, error: '本月预算不足' };
  await login(page, 'news=51');
  await expect(page.getByText(/预算不足，等待下月/)).toBeVisible();
  await expect(page.getByRole('button', { name: '请求事件解读' })).toHaveCount(0);
  state.job = {
    ...completed,
    status: 'invalid_output',
    result: null,
    error: '引用未通过校验',
    attempts: 3,
  };
  await page.getByRole('button', { name: '刷新解读' }).click();
  await expect(page.getByText('引用未通过校验', { exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: '潜在影响 · 模型推断' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: '重试解读', exact: true })).toHaveCount(0);
  expect(state.posts).toHaveLength(0);
});
