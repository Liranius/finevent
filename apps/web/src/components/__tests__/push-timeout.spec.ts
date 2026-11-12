import { afterEach, expect, it, vi } from 'vitest';

import { pushDeadline } from '../../push-timeout';

afterEach(() => vi.useRealTimers());

it('订阅超时会结束界面等待，迟到结果不会变成成功', async () => {
  vi.useFakeTimers();
  let resolve!: (value: string) => void;
  const operation = new Promise<string>((done) => {
    resolve = done;
  });
  const waiting = pushDeadline(operation, 45000, '连接超时').catch((cause: unknown) => cause);
  await vi.advanceTimersByTimeAsync(45000);
  expect(await waiting).toEqual(new Error('连接超时'));
  resolve('late');
  await operation;
  expect(vi.getTimerCount()).toBe(0);
});

it('订阅成功和失败均清除定时器并保留实际结果', async () => {
  vi.useFakeTimers();
  await expect(pushDeadline(Promise.resolve('subscription'), 45000, 'timeout')).resolves.toBe(
    'subscription',
  );
  await expect(
    pushDeadline(Promise.reject(new Error('permission denied')), 45000, 'timeout'),
  ).rejects.toThrow('permission denied');
  expect(vi.getTimerCount()).toBe(0);
});
