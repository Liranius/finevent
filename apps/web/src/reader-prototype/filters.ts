import { defaults, searchFields } from '../news-search';
import type { ReaderFilters } from '../news-search';
import { stories, type Story } from './stories';
export { defaults } from '../news-search';

function choices(values: string[]) {
  return [...new Set(values)].map((value) => ({ label: value, value }));
}
export const fields = searchFields(
  choices(stories.map((s) => s.category)),
  choices(stories.flatMap((s) => s.industries)),
  choices(stories.map((s) => s.source)),
);
// A fixed clock makes a historical edition browsable on any day.
export const sampleCutoff = '2024-10-18T10:06';
const cutoffMs = Date.parse(sampleCutoff + '+08:00');

export function restoreFilters(state: Record<string, unknown>) {
  const restored = { ...defaults };
  for (const field of fields) {
    const value = state[field.key];
    if (typeof value === 'string' && field.options.some((option) => option.value === value))
      restored[field.key] = value;
  }
  return restored;
}

export function storyTimestamp(item: Story, order: string) {
  if (order === 'discovered') return item.recordedAt;
  return item.time ? item.day + 'T' + item.time : '';
}

export function filterStories(filters: ReaderFilters, query: string) {
  const keyword = query.trim().toLocaleLowerCase();
  const earliest = cutoffMs - Number(filters.range) * 86400000;
  return stories
    .filter(
      (item) =>
        (!filters.category || item.category === filters.category) &&
        (!filters.industry || item.industries.includes(filters.industry)) &&
        (!filters.source || item.source === filters.source) &&
        item.importance >= Number(filters.importance) &&
        (filters.range === '0' || Date.parse(item.recordedAt + '+08:00') >= earliest) &&
        [item.title, item.source, ...item.paragraphs].some((text) =>
          text.toLocaleLowerCase().includes(keyword),
        ),
    )
    .sort((a, b) =>
      storyTimestamp(b, filters.order).localeCompare(storyTimestamp(a, filters.order)),
    );
}
