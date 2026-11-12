import { flushPromises, mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { api } from '../../api';
import type { ReminderData } from '../../reminders';
import ReminderSettings from '../ReminderSettings.vue';

vi.mock('../../api', async (original) => ({
  ...(await original<typeof import('../../api')>()),
  api: vi.fn<typeof api>(),
}));
const fixture: ReminderData = {
  settings: {
    enabled: false,
    devices: [],
    kinds: [],
    fund_codes: [],
    early_hours: 24,
    late_minutes: 30,
    date_previous: true,
    date_same: true,
    fund_end: true,
  },
  devices: [],
  watches: [],
  upcoming: [],
  history: [],
};
beforeEach(() => {
  vi.useFakeTimers();
  vi.mocked(api).mockResolvedValue(structuredClone(fixture));
});
afterEach(() => {
  vi.useRealTimers();
  vi.resetAllMocks();
});

describe('reminder settings', () => {
  it('keeps unsaved edits and validation errors while records refresh', async () => {
    const wrapper = mount(ReminderSettings);
    await flushPromises();
    await wrapper.get('#reminder-funds').setValue('12');
    await wrapper.get('form').trigger('submit');
    expect(wrapper.get('[role="alert"]').text()).toContain('六位基金代码');
    await vi.advanceTimersByTimeAsync(30000);
    await flushPromises();
    expect((wrapper.get('#reminder-funds').element as HTMLInputElement).value).toBe('12');
    expect(wrapper.get('[role="alert"]').text()).toContain('六位基金代码');
    expect(vi.mocked(api).mock.calls.every((call) => call[1] !== 'PUT')).toBe(true);
    wrapper.unmount();
  });
  it('shows accepted and device-receipt evidence separately', async () => {
    const record = {
      id: 1,
      event_id: 'event',
      title: '测试日程',
      body: '',
      scheduled_at: 100,
      due: 100,
      status: 'accepted',
      label: '测试设备',
      accepted_at: 101,
      received_at: 0,
      clicked_at: 0,
      error: '',
    };
    vi.mocked(api).mockResolvedValue({ ...fixture, history: [record] });
    const wrapper = mount(ReminderSettings, { props: { recordsOnly: true } });
    await flushPromises();
    expect(wrapper.find('.reminder-history strong').text()).toBe('推送服务已接受');
    vi.mocked(api).mockResolvedValue({ ...fixture, history: [{ ...record, received_at: 102 }] });
    await vi.advanceTimersByTimeAsync(30000);
    await flushPromises();
    expect(wrapper.find('.reminder-history strong').text()).toBe('设备已回执');
    wrapper.unmount();
  });
});
