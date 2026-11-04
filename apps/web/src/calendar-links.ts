import type { CalendarEvent } from './calendar.js';
import type { NewsEntry } from './news.js';

export interface CalendarLink {
  id: number;
  news: NewsEntry;
  event: CalendarEvent;
  note: string;
  confirmed_at: number;
  news_snapshot: NewsEntry;
  event_snapshot: CalendarEvent;
  changed: boolean;
}
export interface CalendarLinkPage {
  items: CalendarLink[];
  next: number;
}
