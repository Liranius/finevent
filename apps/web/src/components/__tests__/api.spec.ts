import { afterEach, describe, expect, it, vi } from 'vitest';

import { api, ApiError, deliveryState, formatTime, vapidBytes, type Delivery } from '../../api';

afterEach(() => vi.unstubAllGlobals());
describe('客户端记录语义', () => {
  it('只被服务接受不能显示为设备已收到', () => {
    const item: Delivery = {
      id: 1,
      label: 'phone',
      scenario: 'locked',
      status: 'accepted',
      due: 0,
      accepted_at: 1,
      received_at: 0,
      clicked_at: 0,
      error: '',
      manual_result: 'pending',
    };
    expect(deliveryState(item)).toBe('服务已接受');
    expect(deliveryState({ ...item, received_at: 2 })).toBe('设备已收到');
    expect(deliveryState({ ...item, clicked_at: 3 })).toBe('已点击');
  });
  it('时间显式使用北京时间，缺失不伪造', () => {
    expect(formatTime(0)).toBe('尚无记录');
    expect(formatTime(Date.parse('2026-10-26T01:30:00Z') / 1000)).toContain('09:30:00');
  });
  it('保留错误状态以处理会话失效', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(new Response(JSON.stringify({ error: '请先登录' }), { status: 401 })),
    );
    await expect(api('status')).rejects.toEqual(new ApiError('请先登录', 401));
  });
  it('VAPID base64url 转换保持字节', () => {
    expect([...vapidBytes('AAH-_w')]).toEqual([0, 1, 254, 255]);
  });
});
