import type { Device } from './api';
import type { Topic } from './news';
import type { ReminderRecord } from './reminders';

export interface NewsPushSettings {
  enabled: boolean;
  devices: number[];
  categories: string[];
  industries: string[];
}
export interface NewsPushData {
  settings: NewsPushSettings;
  enabled_at: number;
  backlog: number;
  categories: Topic[];
  industries: Topic[];
  devices: Device[];
  history: (Omit<ReminderRecord, 'event_id' | 'scheduled_at'> & {
    news_id: number | null;
    version: number;
    created: number;
    expires: number;
    reason: string;
  })[];
}
