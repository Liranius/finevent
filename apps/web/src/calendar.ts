export interface CalendarEvent {
  id: string;
  source: string;
  source_id: string;
  title: string;
  kind: string;
  start: string;
  end: string;
  timezone: string;
  precision: 'date' | 'minute';
  status: string;
  importance: number;
  url: string;
  evidence: string;
  fund_code: string;
  conditions: string;
  version: number;
  updated_at: number;
  provenance?: {
    mode: 'snapshot' | 'supplement';
    snapshot_id?: string;
    retrieved_at?: number;
    reviewed_at?: number;
    sha256?: string;
    authority_url?: string;
  };
}
export interface CalendarSource {
  id: string;
  name: string;
  url: string;
  last_attempt: number;
  last_success: number;
  error: string;
  count: number;
  coverage_end: string;
  mode?: 'supplement';
  note?: string;
  snapshots?: {
    id: string;
    url: string;
    retrieved_at: number;
    reviewed_at: number;
    sha256: string;
    imported_at: number;
    entries: number;
    imported_entries: number;
    active_entries: number;
    coverage_end: string;
  }[];
}
export interface CalendarData {
  events: CalendarEvent[];
  sources: CalendarSource[];
  sync_enabled: boolean;
}
export interface FundDraft {
  id: string;
  text: string;
  url: string;
  event: CalendarEvent;
  warnings: string[];
  confirmed_id: string;
}
export interface EventDetail {
  event: CalendarEvent;
  revisions: { event: CalendarEvent; saved_at: number }[];
}
export const states: Record<string, string> = {
  scheduled: '预定',
  confirmed: '已确认',
  rescheduled: '已改期',
  cancelled: '已取消',
};
export const sourceNames: Record<string, string> = {
  nbs: '国家统计局',
  fomc: '美联储',
  bls: 'BLS',
  bea: 'BEA',
  nyfed: '纽约联储',
  fund: '基金公告',
  manual: '手工补录',
};
export function eventOrigin(event: CalendarEvent): string {
  const name = sourceNames[event.source] ?? event.source;
  if (event.provenance?.mode === 'snapshot') return `${name} · 官方快照`;
  if (event.provenance?.mode === 'supplement') return `${name} · 补充来源`;
  return name;
}
export function reviewedTime(value: number | undefined): string {
  if (!value) return '未知';
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(new Date(value * 1000));
}
export function beijingDay(value: string | Date): string {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(new Date(value));
  return ['year', 'month', 'day']
    .map((type) => parts.find((part) => part.type === type)?.value)
    .join('-');
}
export function eventDays(event: CalendarEvent): [string, string] {
  return event.precision === 'date'
    ? [event.start, event.end]
    : [beijingDay(event.start), beijingDay(event.end)];
}
export function eventTime(event: CalendarEvent): string {
  if (event.precision === 'date')
    return `${event.start}${event.end !== event.start ? ` 至 ${event.end}` : ''} · 仅日期`;
  const format = (value: string) =>
    new Intl.DateTimeFormat('zh-CN', {
      timeZone: 'Asia/Shanghai',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    }).format(new Date(value));
  return `${format(event.start)}${event.start !== event.end ? ` 至 ${format(event.end)}` : ''} · 北京时间`;
}
const calendarDayLabel = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'UTC',
  month: 'long',
  day: 'numeric',
  weekday: 'long',
});
export function dayLabel(day: string) {
  return calendarDayLabel.format(new Date(day + 'T00:00:00Z'));
}
export function agendaTime(event: CalendarEvent): string {
  if (event.start !== event.end) return eventTime(event);
  if (event.precision === 'date') return '仅日期';
  return (
    new Intl.DateTimeFormat('zh-CN', {
      timeZone: 'Asia/Shanghai',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    }).format(new Date(event.start)) + ' · 北京时间'
  );
}
export function calendarEntries(events: CalendarEvent[]) {
  return events
    .map((event) => {
      const [start, end] = eventDays(event);
      return { event, start, end };
    })
    .sort(
      (a, b) =>
        a.start.localeCompare(b.start) ||
        (a.event.precision === b.event.precision
          ? Date.parse(a.event.start) - Date.parse(b.event.start)
          : a.event.precision === 'date'
            ? -1
            : 1) ||
        a.event.title.localeCompare(b.event.title, 'zh-CN') ||
        a.event.id.localeCompare(b.event.id),
    );
}
export function monthAgenda(entries: ReturnType<typeof calendarEntries>, month: string) {
  const first = month + '-01';
  const groups = new Map<string, CalendarEvent[]>();
  for (const { event, start, end } of entries) {
    if (end < first || start.slice(0, 7) > month) continue;
    const day = start < first ? first : start;
    const group = groups.get(day) ?? [];
    group.push(event);
    groups.set(day, group);
  }
  return Array.from(groups, ([day, events]) => ({ day, events }));
}
export function monthDays(month: string): string[] {
  const first = new Date(`${month}-01T00:00:00Z`);
  const offset = (first.getUTCDay() + 6) % 7;
  return Array.from({ length: 42 }, (_, i) =>
    new Date(first.getTime() + (i - offset) * 86400000).toISOString().slice(0, 10),
  );
}
export function blankEvent(): CalendarEvent {
  return {
    id: '',
    source: 'manual',
    source_id: '',
    title: '',
    kind: 'other',
    start: '',
    end: '',
    timezone: 'Asia/Shanghai',
    precision: 'date',
    status: 'scheduled',
    importance: 2,
    url: '',
    evidence: '',
    fund_code: '',
    conditions: '',
    version: 0,
    updated_at: 0,
  };
}
