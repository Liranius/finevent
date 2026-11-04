import { describe, expect, it } from 'vitest';

import { firstSeenName, newsTime, safeNewsURL, sourceName, topicNames } from '../../news';

describe('新闻来源与时间展示', () => {
  it('保留未知发布时间并统一使用北京时间', () => {
    expect(newsTime(0)).toBe('未知');
    expect(newsTime(null)).toBe('未知');
    expect(newsTime(Date.parse('2026-11-01T20:30:00Z') / 1000)).toBe('2026/11/02 04:30:00');
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
