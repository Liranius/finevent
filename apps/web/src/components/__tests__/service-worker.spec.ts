// @vitest-environment node
import { readFileSync } from 'node:fs';
import { createContext, runInContext } from 'node:vm';

import { describe, expect, it, vi } from 'vitest';

interface WindowClientStub {
  url: string;
  focus: () => Promise<WindowClientStub>;
  navigate: (url: string) => Promise<WindowClientStub | null>;
}
interface ClickEventStub {
  notification: { data: unknown; close: () => void };
  waitUntil: (promise: Promise<unknown>) => void;
}
const origin = 'https://www.example.com';
const data = { id: 42, url: '/finevent/?delivery=42', receipt_token: 'test-only-token' };
const script = readFileSync(new URL('../../../public/sw.js', import.meta.url), 'utf8');

function windowClient(path = '/finevent/') {
  const client: WindowClientStub = {
    url: origin + path,
    focus: vi.fn<WindowClientStub['focus']>(() => Promise.resolve(client)),
    navigate: vi.fn<WindowClientStub['navigate']>(() => Promise.resolve(client)),
  };
  return client;
}
function worker() {
  const listeners = new Map<string, (event: ClickEventStub) => void>();
  const openWindow = vi.fn<(url: string) => Promise<WindowClientStub | null>>();
  const matchAll = vi.fn<() => Promise<WindowClientStub[]>>().mockResolvedValue([]);
  const context = createContext({
    URL,
    self: {
      location: { origin },
      clients: { openWindow, matchAll },
      addEventListener: (name: string, listener: (event: ClickEventStub) => void) =>
        listeners.set(name, listener),
    },
  });
  runInContext(script, context);
  // Isolate window activation from the already separate receipt persistence/network path.
  const acknowledge = vi
    .fn<(data: unknown, kind: string) => Promise<void>>()
    .mockResolvedValue(undefined);
  context.acknowledge = acknowledge;
  function click(payload: unknown = data) {
    const waits: Promise<unknown>[] = [];
    const close = vi.fn<() => void>();
    const listener = listeners.get('notificationclick');
    if (!listener) throw new Error('Missing notification click listener');
    listener({
      notification: { data: payload, close },
      waitUntil: (promise) => waits.push(promise),
    });
    return { close, finished: Promise.allSettled(waits) };
  }
  return { openWindow, matchAll, acknowledge, click };
}

describe('通知点击窗口激活', () => {
  it('新闻通知保留详情哈希并唤起应用', async () => {
    const app = worker();
    app.openWindow.mockResolvedValue(windowClient());
    const payload = { ...data, url: '/finevent/#news=123' };
    await app.click(payload).finished;
    expect(app.openWindow).toHaveBeenCalledWith(origin + payload.url);
    expect(app.acknowledge).toHaveBeenCalledWith(payload, 'clicked');
  });
  it('立即请求打开应用，不先等待枚举或重载旧窗口', async () => {
    const app = worker();
    const opened = windowClient();
    app.openWindow.mockResolvedValue(opened);
    const click = app.click();
    expect(app.openWindow).toHaveBeenCalledWith(origin + data.url);
    expect(app.matchAll).not.toHaveBeenCalled();
    await click.finished;
    expect(click.close).toHaveBeenCalledOnce();
    expect(opened.focus).toHaveBeenCalledOnce();
  });

  it('打开窗口仍在等待时也保存点击回执', async () => {
    const app = worker();
    let resolve!: (client: WindowClientStub) => void;
    app.openWindow.mockReturnValue(new Promise((done) => (resolve = done)));
    const click = app.click();
    expect(app.acknowledge).toHaveBeenCalledWith(data, 'clicked');
    resolve(windowClient());
    await click.finished;
  });

  it('打开失败时先聚焦现有窗口，再导航到通知地址', async () => {
    const app = worker();
    const client = windowClient();
    app.openWindow.mockRejectedValue(new Error('Cannot open window'));
    app.matchAll.mockResolvedValue([client]);
    await app.click().finished;
    expect(client.focus).toHaveBeenCalledOnce();
    expect(client.navigate).toHaveBeenCalledWith(origin + data.url);
    expect(vi.mocked(client.focus).mock.invocationCallOrder[0]).toBeLessThan(
      vi.mocked(client.navigate).mock.invocationCallOrder[0]!,
    );
  });

  it('已打开应用后聚焦失败，不重复新建窗口', async () => {
    const app = worker();
    const client = windowClient();
    vi.mocked(client.focus).mockRejectedValue(new Error('Activation already consumed'));
    app.openWindow.mockResolvedValue(client);
    const results = await app.click().finished;
    expect(results.every((result) => result.status === 'fulfilled')).toBe(true);
    expect(app.openWindow).toHaveBeenCalledOnce();
    expect(app.matchAll).not.toHaveBeenCalled();
  });

  it('跳过消失的窗口和其他应用，回退窗口导航失败仍保留聚焦', async () => {
    const app = worker();
    const disappeared = windowClient();
    const otherApp = windowClient('/TrendRadar/');
    const active = windowClient();
    vi.mocked(disappeared.focus).mockRejectedValue(new Error('Window closed'));
    vi.mocked(active.navigate).mockRejectedValue(new Error('Network unavailable'));
    app.openWindow.mockResolvedValue(null);
    app.matchAll.mockResolvedValue([disappeared, otherApp, active]);
    const results = await app.click().finished;
    expect(otherApp.focus).not.toHaveBeenCalled();
    expect(active.focus).toHaveBeenCalledOnce();
    expect(results.every((result) => result.status === 'fulfilled')).toBe(true);
  });

  it('窗口全部不可用时保留点击回执并报告打开失败', async () => {
    const app = worker();
    app.openWindow.mockRejectedValue(new Error('Blocked'));
    const results = await app.click().finished;
    expect(results.some((result) => result.status === 'rejected')).toBe(true);
    expect(app.acknowledge).toHaveBeenCalledWith(data, 'clicked');
  });

  it.each(['https://example.com/finevent/', '/TrendRadar/', 'javascript:alert(1)'])(
    '拒绝通知跳转到应用范围外：%s',
    async (url) => {
      const app = worker();
      await app.click({ ...data, url }).finished;
      expect(app.openWindow).not.toHaveBeenCalled();
      expect(app.matchAll).not.toHaveBeenCalled();
    },
  );
});
