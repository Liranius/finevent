import type { Locator } from '@playwright/test';

export const readerDevices = [
  {
    name: '桌面',
    viewport: { width: 1366, height: 900 },
    hasTouch: false,
    activate: (target: Locator) => target.press('Enter'),
  },
  {
    name: '手机',
    viewport: { width: 390, height: 844 },
    hasTouch: true,
    activate: (target: Locator) => target.tap(),
  },
];
