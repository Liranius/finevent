import { describe, expect, it } from 'vitest';
import { createMemoryHistory } from 'vue-router';

import { createAppRouter } from '../../router';

describe('通知与深链接兼容', () => {
  it.each([
    ['/', '/news'],
    ['/news', '/news'],
    ['/news=', '/news'],
    ['/news=321', '/news/321'],
    ['/event=calendar-1', '/calendar/events/calendar-1'],
    ['/draft=fund-1', '/calendar/fund/fund-1'],
    ['/news-push', '/news/push'],
    ['/ai', '/ai'],
    ['/verification', '/verification/push'],
    ['/news=invalid', '/not-found'],
    ['/unknown', '/not-found'],
    ['/verification/unknown', '/not-found'],
  ])('将 %s 定位到 %s', async (link, path) => {
    const router = createAppRouter(createMemoryHistory());
    await router.push(link);
    expect(router.currentRoute.value.fullPath).toBe(path);
    expect(router.currentRoute.value.matched.at(-1)?.components?.default).toBeDefined();
  });

  it('旧链接中的编码标识只解码一次，仍作为一个路由参数传递', async () => {
    const router = createAppRouter(createMemoryHistory());
    await router.push('/event=source%2Fitem%26revision%3D2');
    expect(router.currentRoute.value.name).toBe('calendar-event');
    expect(router.currentRoute.value.params.id).toBe('source/item&revision=2');
  });
});
