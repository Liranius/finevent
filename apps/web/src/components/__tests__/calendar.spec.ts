import { describe, expect, it } from 'vitest';

import {
  beijingDay,
  calendarEntries,
  monthAgenda,
  agendaTime,
  blankEvent,
  eventDays,
  eventTime,
  eventOrigin,
  reviewedTime,
  monthDays,
} from '../../calendar';

describe('calendar time semantics', () => {
  it('distinguishes reviewed snapshots and supplement events from direct sources', () => {
    expect(eventOrigin({ ...blankEvent(), source: 'nbs', provenance: { mode: 'snapshot' } })).toBe(
      '国家统计局 · 官方快照',
    );
    expect(
      eventOrigin({ ...blankEvent(), source: 'nyfed', provenance: { mode: 'supplement' } }),
    ).toBe('纽约联储 · 补充来源');
    expect(eventOrigin({ ...blankEvent(), source: 'nbs' })).toBe('国家统计局');
  });
  it('keeps a year and Beijing timezone in snapshot review evidence', () => {
    expect(reviewedTime(Date.parse('2026-10-30T13:00:00Z') / 1000)).toBe('2026/10/30 21:00');
    expect(reviewedTime(undefined)).toBe('未知');
  });
  it('keeps date-only events in their source civil dates', () => {
    const event = {
      ...blankEvent(),
      start: '2026-11-01',
      end: '2026-11-02',
      timezone: 'America/New_York',
    };
    expect(eventDays(event)).toEqual(['2026-11-01', '2026-11-02']);
    expect(eventTime(event)).toContain('仅日期');
  });
  it('converts explicit offsets across DST and Beijing midnight', () => {
    expect(beijingDay('2026-11-06T20:00:00-05:00')).toBe('2026-11-07');
    const event = {
      ...blankEvent(),
      precision: 'minute' as const,
      start: '2026-10-30T08:30:00-04:00',
      end: '2026-10-30T08:30:00-04:00',
    };
    expect(eventTime(event)).toContain('20:30');
    event.start = event.end = '2026-11-06T08:30:00-05:00';
    expect(eventTime(event)).toContain('21:30');
  });
  it('groups spanning events once at the first visible day and orders instants by Beijing time', () => {
    const event = { ...blankEvent(), start: '2028-01-30', end: '2028-02-02', id: 'carry' };
    const later = {
      ...event,
      id: 'later',
      precision: 'minute' as const,
      start: '2028-02-01T00:30:00-05:00',
      end: '2028-02-01T00:30:00-05:00',
    };
    const early = {
      ...later,
      id: 'early',
      start: '2028-02-01T08:00:00+08:00',
      end: '2028-02-01T08:00:00+08:00',
    };
    const leap = { ...event, id: 'leap', start: '2028-02-29', end: '2028-02-29' };
    const outside = { ...event, start: '2028-03-01', end: '2028-03-01' };
    const groups = monthAgenda(calendarEntries([later, leap, outside, event, early]), '2028-02');
    expect(groups.map((group) => [group.day, group.events.map((item) => item.id)])).toEqual([
      ['2028-02-01', ['carry', 'early', 'later']],
      ['2028-02-29', ['leap']],
    ]);
    expect(agendaTime(later)).toBe('13:30 · 北京时间');
    expect(agendaTime(leap)).toBe('仅日期');
    expect(agendaTime(event)).toContain('2028-01-30 至 2028-02-02');
  });
  it('lays out a full Monday-first leap-month grid', () => {
    const days = monthDays('2028-02');
    expect(days).toHaveLength(42);
    expect(days[0]).toBe('2028-01-31');
    expect(days).toContain('2028-02-29');
    expect(new Set(days).size).toBe(42);
  });
});
