import type { Device } from './api';

export interface ReminderSettings {
  enabled: boolean;
  devices: number[];
  kinds: string[];
  fund_codes: string[];
  early_hours: number;
  late_minutes: number;
  date_previous: boolean;
  date_same: boolean;
  fund_end: boolean;
}
export interface ReminderRecord {
  id: number;
  event_id: string;
  title: string;
  body: string;
  scheduled_at: number;
  due: number;
  status: string;
  label: string;
  accepted_at: number;
  received_at: number;
  clicked_at: number;
  error: string;
}
export interface ReminderData {
  settings: ReminderSettings;
  devices: Device[];
  watches: { event_id: string; enabled: number; title: string }[];
  upcoming: ReminderRecord[];
  history: ReminderRecord[];
}
export interface EventReminder {
  followed: boolean;
  override: boolean | null;
  enabled: boolean;
  active_devices: number;
  upcoming: Pick<ReminderRecord, 'id' | 'scheduled_at' | 'label' | 'status'>[];
}
export function reminderState(
  item: Pick<ReminderRecord, 'status' | 'clicked_at' | 'received_at' | 'accepted_at'>,
): string {
  if (item.clicked_at) return '已点击';
  if (item.received_at) return '设备已回执';
  if (item.accepted_at) return '推送服务已接受';
  return (
    (
      {
        queued: '待发送',
        sending: '发送中',
        cancelled: '已撤销',
        expired: '已过期',
        failed: '发送失败',
      } as Record<string, string>
    )[item.status] ?? item.status
  );
}
