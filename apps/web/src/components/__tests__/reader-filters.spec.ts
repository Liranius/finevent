import { describe, expect, it } from 'vitest';

import { defaults, filterStories, restoreFilters } from '../../reader-prototype/filters';

describe('报道原型高级搜索', () => {
  it('组合筛选以首次记录时间确定范围，即使改为按发布时间排序', () => {
    const selected = {
      category: '宏观经济',
      industry: '消费',
      importance: '3',
      source: '美国劳工统计局',
      range: '7',
      order: 'published',
    };
    expect(filterStories(selected, 'CPI').map((item) => item.id)).toEqual(['us-cpi']);
    expect(filterStories({ ...selected, range: '1' }, 'CPI')).toEqual([]);
  });

  it('未知发布时刻置后，切换排序不改变原始记录或混用两个时间', () => {
    const published = filterStories({ ...defaults, order: 'published' }, '');
    expect(published.at(-1)?.id).toBe('ecb-rates');
    const discovered = filterStories(defaults, '');
    expect(discovered.findIndex((s) => s.id === 'ecb-rates')).toBeLessThan(
      discovered.findIndex((s) => s.id === 'us-jobs'),
    );
  });

  it('从旧原型的历史记录返回时丢弃失效条件，但保留仍有效的选择', () => {
    expect(
      restoreFilters({ category: '失效类别', source: '国家统计局', range: '7', topic: '旧主题' }),
    ).toEqual({ ...defaults, source: '国家统计局', range: '7' });
  });
});
