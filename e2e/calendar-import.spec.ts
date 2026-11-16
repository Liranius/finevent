import { expect, test } from '@playwright/test';

for (const width of [1280, 393]) {
  test(`BLS ICS · ${width}px 预览、放弃、导入、改期与重复文件`, async ({ page }) => {
    await page.setViewportSize({ width, height: 852 });
    await page.goto('./#/settings/calendar-sources');
    await page.getByLabel('访问密码').fill('finevent-e2e-password-only');
    await page.getByRole('button', { name: '进入工作台' }).click();
    const file = page.getByLabel('选择 ICS 文件');
    const upload = (text: string) =>
      file.setInputFiles({
        name: 'calendar.ics',
        mimeType: 'text/calendar',
        buffer: Buffer.from(text),
      });
    const body = [
      'BEGIN:VCALENDAR',
      'VERSION:2.0',
      'BEGIN:VEVENT',
      `UID:preview-${width}`,
      `SUMMARY:Calendar preview ${width}`,
      'DTSTART;TZID=America/New_York:20280107T083000',
      'END:VEVENT',
      'END:VCALENDAR',
      '',
    ].join('\r\n');
    await upload('<html>403</html>');
    await expect(page.getByRole('alert')).toContainText('未返回 iCalendar');
    await upload(body);
    await expect(page.getByRole('heading', { name: '导入预览 · calendar.ics' })).toBeFocused();
    await expect(page.getByText(`新增 · Calendar preview ${width}`)).toBeVisible();
    await page.getByRole('button', { name: '放弃本次导入' }).press('Enter');
    await expect(file).toBeFocused();
    await upload(body);
    const apply = page.getByRole('button', { name: '确认导入 1 项' });
    await apply.press('Enter');
    await expect(page.getByText('已导入 1 项日程变更。')).toBeFocused();
    const changed = body.replace('20280107T083000', '20280110T083000');
    await upload(changed);
    await expect(page.getByText('原定 2028/01/07 21:30 · 北京时间')).toBeVisible();
    await expect(page.getByText('改为 2028/01/10 21:30 · 北京时间')).toBeVisible();
    await apply.click();
    await expect(page.getByText('已导入 1 项日程变更。')).toBeVisible();
    await upload(changed);
    await expect(page.getByText('此文件已导入。')).toBeVisible();
    await expect(apply).toHaveCount(0);
    await page.reload();
    const sources = page.locator('summary').filter({ hasText: '官方来源' });
    await sources.press('Enter');
    await expect(page.getByRole('link', { name: '下载已导入的 ICS' }).first()).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true,
    );
  });
}
