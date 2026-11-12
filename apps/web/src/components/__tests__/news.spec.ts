import { describe, expect, it } from 'vitest';

import {
  firstSeenName,
  newsRowTimes,
  newsTime,
  safeNewsURL,
  sourceName,
  topicNames,
} from '../../news';

describe('新闻来源与时间展示', () => {
  it('保留未知发布时间并统一使用北京时间', () => {
    expect(newsTime(0)).toBe('未知');
    expect(newsTime(null)).toBe('未知');
    expect(newsTime(Date.parse('2026-11-01T20:30:00Z') / 1000)).toBe('2026/11/02 04:30:00');
  });
  it('按北京时间识别跨年，保留发布时间的年份', () => {
    const item = {
      published_at: Date.parse('2026-12-31T15:59:59Z') / 1000,
      first_seen: Date.parse('2026-12-31T16:00:01Z') / 1000,
    };
    expect(newsRowTimes(item, false)).toEqual({
      day: '2027/01/01',
      clock: '00:00',
      secondary: '发布 2026/12/31 23:59',
      earlierPublication: '2026/12/31发布',
    });
    expect(newsRowTimes(item, true)).toEqual({
      day: '2026/12/31',
      clock: '23:59',
      secondary: '首次记录 2027/01/01 00:00',
      earlierPublication: '',
    });
  });
  it('北京时间同日及发布时间晚于首次记录时均不显示早先发布注记', () => {
    const first_seen = Date.parse('2026-11-11T17:00:00Z') / 1000;
    expect(newsRowTimes({ first_seen, published_at: first_seen - 1800 }, false).secondary).toBe(
      '发布 00:30',
    );
    expect(
      newsRowTimes({ first_seen, published_at: first_seen - 1800 }, false).earlierPublication,
    ).toBe('');
    expect(
      newsRowTimes({ first_seen, published_at: first_seen + 86400 }, false).earlierPublication,
    ).toBe('');
  });
  it('原文链接拒绝脚本、相对地址和内嵌凭据', () => {
    for (const url of [
      'javascript:alert(1)',
      'data:text/html,test',
      '/admin',
      'https://user:pass@example.org',
    ])
      expect(safeNewsURL(url)).toBe('');
    expect(safeNewsURL('https://example.org/news?id=1')).toBe('https://example.org/news?id=1');
  });
  it('明确区分直接快讯和 TrendRadar 通道', () => {
    expect(sourceName('wallstreetcn')).toBe('华尔街见闻快讯');
    expect(sourceName('trendradar:wallstreetcn-quick')).toBe('华尔街见闻快讯 · TrendRadar');
    expect(sourceName('other')).toBe('other');
    expect(firstSeenName('wallstreetcn')).toBe('首次发现');
    expect(firstSeenName('trendradar:cls-telegraph')).toBe('上游首次记录');
    expect(topicNames(['macro'], [{ id: 'macro', name: '宏观经济' }])).toBe('宏观经济');
  });
});
