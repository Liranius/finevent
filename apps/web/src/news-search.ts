import type { NewsNavigation } from './workspace-navigation';

export type ReaderFilters = Omit<NewsNavigation['filters'], 'q'>;
export type FilterKey = keyof ReaderFilters;
interface Choice {
  value: string;
  label: string;
}
interface Field {
  key: FilterKey;
  label: string;
  options: Choice[];
}

export const defaults: ReaderFilters = {
  category: '',
  industry: '',
  importance: '0',
  source: '',
  range: '0',
  order: 'discovered',
};

export function searchFields(
  categories: Choice[],
  industries: Choice[],
  sources: Choice[],
): Field[] {
  return [
    {
      key: 'category',
      label: '类别',
      options: [{ value: '', label: '全部类别' }, ...categories],
    },
    {
      key: 'industry',
      label: '提及行业',
      options: [{ value: '', label: '全部行业' }, ...industries],
    },
    {
      key: 'importance',
      label: '规则重要性',
      options: [
        { value: '0', label: '全部重要性' },
        { value: '2', label: '关注及以上' },
        { value: '3', label: '重要' },
      ],
    },
    {
      key: 'source',
      label: '来源',
      options: [{ value: '', label: '全部来源' }, ...sources],
    },
    {
      key: 'range',
      label: '首次记录范围',
      options: [
        { value: '0', label: '全部留存' },
        { value: '1', label: '近 24 小时' },
        { value: '7', label: '近 7 天' },
        { value: '30', label: '近 30 天' },
      ],
    },
    {
      key: 'order',
      label: '排序',
      options: [
        { value: 'discovered', label: '首次记录优先' },
        { value: 'published', label: '来源发布优先' },
      ],
    },
  ];
}
