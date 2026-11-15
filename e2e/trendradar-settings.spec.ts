import { expect, test } from '@playwright/test';

test('TrendRadar 设置可独立保存、处理冲突并撤销，保留另一项草稿', async ({ page }, testInfo) => {
  const settings = {
    sources: [
      { id: 'cls-hot', name: '财联社热门', enabled: true },
      { id: 'v2ex', name: 'V2EX', enabled: true },
    ],
    keywords: '[WORD_GROUPS]\n能源',
    source_revision: 'a'.repeat(64),
    keyword_revision: 'b'.repeat(64),
    undo_sources: false,
    undo_keywords: false,
  };
  let conflict = true;
  let healthReads = 0;
  const revisions: string[] = [];
  await page.route('**/api/v1/news/source-health', (route) => {
    healthReads++;
    const now = Math.floor(Date.now() / 1000);
    return route.fulfill({
      json: {
        config_known: true,
        collection_enabled: true,
        trendradar_enabled: true,
        read_success: now,
        latest_cycle: now - 60,
        warnings: [],
        sources: settings.sources.map((s) => ({
          id: s.id,
          state: s.enabled ? 'healthy' : 'disabled',
          last_attempt: now - 60,
          last_success: now - 60,
          last_failure: 0,
          last_status: 'success',
          recovered_at: 0,
          stale: false,
        })),
      },
    });
  });
  await page.route('**/api/v1/news/trendradar-settings', async (route) => {
    if (route.request().method() === 'PUT') {
      const change = route.request().postDataJSON() as {
        section: string;
        value: string | string[];
        undo?: boolean;
        revision: string;
      };
      revisions.push(change.revision);
      if (change.section === 'sources') {
        settings.sources[1].enabled = change.undo === true || change.value.includes('v2ex');
        settings.undo_sources = true;
        settings.source_revision = 'c'.repeat(64);
      } else if (conflict) {
        conflict = false;
        settings.keywords = '外部更新';
        settings.keyword_revision = 'd'.repeat(64);
        await route.fulfill({ status: 409, json: { error: '配置已更改，请重新读取后再保存。' } });
        return;
      } else {
        settings.keywords = String(change.value);
      }
    }
    await route.fulfill({ json: settings });
  });
  await page.goto('./');
  await page.getByLabel('访问密码').fill('finevent-e2e-password-only');
  await page.getByRole('button', { name: '进入工作台' }).click();
  await page.getByRole('link', { name: '设置', exact: true }).click();
  const link = page.getByRole('link', { name: 'TrendRadar 设置' });
  await link.focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('heading', { name: 'TrendRadar 设置' })).toBeFocused();
  const words = page.getByLabel('关键词规则');
  await words.fill('盈利');
  const help = page.getByRole('button', { name: '日报关键词配置说明' });
  const dialog = page.getByRole('dialog', { name: '日报关键词怎么写' });
  const closeHelp = dialog.getByRole('button', { name: '关闭说明' });
  await help.focus();
  await page.keyboard.press('Enter');
  await expect(closeHelp).toBeFocused();
  await expect(dialog.locator('pre')).toContainText('[WORD_GROUPS]');
  await page.screenshot({ path: testInfo.outputPath('keyword-help-desktop.png') });
  await page.keyboard.press('Escape');
  await expect(dialog).toBeHidden();
  await expect(help).toBeFocused();
  await expect(words).toHaveValue('盈利');
  const v2ex = page.getByRole('checkbox', { name: 'V2EX' });
  await v2ex.focus();
  await page.keyboard.press('Space');
  await page.getByRole('button', { name: '保存来源' }).click();
  await expect(page.getByRole('status')).toContainText('来源已保存');
  await expect(words).toHaveValue('盈利');
  await expect(page.getByText('已启用 1 / 2 个。', { exact: false })).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath('trend-settings-desktop.png'),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await help.click();
  await expect(dialog.getByRole('heading', { name: '示例' })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('keyword-help-mobile.png') });
  await closeHelp.click();
  await expect(dialog).toBeHidden();
  await expect(words).toHaveValue('盈利');
  await page.getByRole('button', { name: '保存关键词' }).click();
  await expect(page.getByRole('alert')).toContainText('配置已更改');
  await expect(words).toHaveValue('盈利');
  await page.getByRole('button', { name: '重新读取关键词' }).click();
  await expect(words).toHaveValue('外部更新');
  await words.fill('盈利');
  await page.getByRole('button', { name: '保存关键词' }).click();
  await expect(page.getByRole('status')).toContainText('关键词已保存');
  await page.getByRole('button', { name: '恢复上一版来源' }).click();
  await expect(v2ex).toBeChecked();
  await expect(page.getByText('已启用 2 / 2 个。', { exact: false })).toBeVisible();
  expect(revisions).toEqual(['a', 'b', 'd', 'c'].map((letter) => letter.repeat(64)));
  await page.reload();
  await expect(words).toHaveValue('盈利');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
  await page.screenshot({ path: testInfo.outputPath('trend-settings-mobile.png'), fullPage: true });
  await v2ex.uncheck();
  await page.getByRole('button', { name: '保存来源' }).click();
  await expect(page.getByRole('status')).toContainText('来源已保存');
  await expect(page.getByRole('heading', { name: 'TrendRadar 来源健康' })).toHaveCount(0);
  expect(healthReads).toBe(0);
  await page.getByRole('link', { name: '返回设置' }).click();
  await page.getByRole('link', { name: '采集通道状态', exact: true }).click();
  await expect(page.getByText('1 个启用来源 · 0 项需核查')).toBeVisible();
  await expect(page.getByText('已停用', { exact: true })).toBeVisible();
});
