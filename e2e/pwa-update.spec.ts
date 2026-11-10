import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { cp, readFile, rm, unlink, utimes, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

import { expect, test } from '@playwright/test';

import { startOfflineServer } from './offline-server.js';

test.use({ serviceWorkers: 'allow' });

test('已有 PWA 能发现资源更新，确认后离线加载新版', async ({
  page,
  context,
  baseURL,
  browserName,
}) => {
  const fixture = resolve(`.validation/pwa-update-${randomUUID()}`);
  const webRoot = resolve(fixture, 'dist');
  await cp('apps/web/dist', webRoot, { recursive: true });
  // Start with the previously shipped worker, which has no build version stamp.
  await cp('apps/web/public/sw.js', resolve(webRoot, 'sw.js'));
  // Model separate releases: HTTP modification dates have one-second precision,
  // while this fixture can copy and replace its files within the same second.
  const previousRelease = new Date(Math.floor(Date.now() / 1000) * 1000 - 60000);
  for (const name of ['sw.js', 'index.html', 'precache.json']) {
    await utimes(resolve(webRoot, name), previousRelease, previousRelease);
  }
  const server = await startOfflineServer(baseURL, webRoot);
  try {
    await page.goto(server.url);
    await expect(page.getByLabel('访问密码')).toBeVisible();
    // Initial activation may reload the app; retry the assertion across that navigation.
    await expect(async () => {
      expect(await page.evaluate(() => navigator.serviceWorker.controller !== null)).toBe(true);
      await expect(page.getByLabel('访问密码')).toBeVisible();
    }).toPass({ timeout: 10000 });
    const previousCaches = await page.evaluate(() => caches.keys());

    const html = await readFile(resolve(webRoot, 'index.html'), 'utf8');
    const entry = html.match(/src="\/finevent\/(assets\/[^"/]+\.js)"/)?.[1];
    assert(entry, 'Missing application entry script');
    const updatedEntry = 'assets/release-update.js';
    await writeFile(
      resolve(webRoot, updatedEntry),
      `${await readFile(resolve(webRoot, entry), 'utf8')}\ndocument.documentElement.dataset.pwaBuild = 'updated';\n`,
    );
    await unlink(resolve(webRoot, entry));
    await writeFile(resolve(webRoot, 'index.html'), html.replace(entry, updatedEntry));
    const build = spawnSync(process.execPath, [resolve('apps/web/scripts/precache.mjs')], {
      cwd: fixture,
      encoding: 'utf8',
    });
    expect({ status: build.status, stderr: build.stderr }).toEqual({ status: 0, stderr: '' });

    const conditional = await page.request.get(`${server.url}sw.js`, {
      headers: { 'If-Modified-Since': previousRelease.toUTCString() },
    });
    expect(conditional.status(), 'The new release must pass HTTP revalidation').toBe(200);

    await page.evaluate(async () => (await navigator.serviceWorker.ready).update());
    await expect(page.getByRole('button', { name: '更新应用', exact: true })).toBeVisible();
    await page.getByRole('button', { name: '更新应用', exact: true }).click();
    await expect(page.locator('html')).toHaveAttribute('data-pwa-build', 'updated');
    const currentCaches = await page.evaluate(() => caches.keys());
    expect(currentCaches).toHaveLength(1);
    expect(currentCaches).not.toEqual(previousCaches);

    await server.stop();
    // The existing offline test documents WebKit's offline-emulation limitation.
    await context.setOffline(browserName !== 'webkit');
    await page.reload();
    await expect(page.getByLabel('访问密码')).toBeVisible();
    await expect(page.locator('html')).toHaveAttribute('data-pwa-build', 'updated');
  } finally {
    await context.setOffline(false);
    await server.stop();
    await rm(fixture, { recursive: true });
  }
});
