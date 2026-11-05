import process from 'node:process';

import { defineConfig, devices } from '@playwright/test';

// A proxy-generated 503 must not replace a real loopback connection failure in offline tests.
const loopbackBypass = [process.env.NO_PROXY, process.env.no_proxy, '127.0.0.1', 'localhost']
  .filter(Boolean)
  .join(',');
process.env.NO_PROXY = loopbackBypass;
process.env.no_proxy = loopbackBypass;

const browsers = [
  { name: 'chromium', port: 4173, device: devices['Desktop Chrome'] },
  { name: 'firefox', port: 4174, device: devices['Desktop Firefox'] },
  { name: 'webkit', port: 4175, device: devices['Desktop Safari'] },
];

export default defineConfig({
  testDir: './e2e',
  timeout: 30000,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 2 : 0,
  // Tests within each browser project share its own Go service and database.
  workers: 1,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    // Route fixtures must not be bypassed by a worker; the offline test opts in explicitly.
    serviceWorkers: 'block',
    headless: true,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: browsers.map(({ name, port, device }) => ({
    name,
    use: { ...device, baseURL: `http://127.0.0.1:${port}/finevent/` },
  })),
  webServer: browsers.map(({ port }) => ({
    command: `node scripts/e2e-server.mjs ${port}`,
    url: `http://127.0.0.1:${port}/finevent/healthz`,
    reuseExistingServer: false,
    timeout: 120000,
  })),
});
