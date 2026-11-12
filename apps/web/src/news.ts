export interface Topic {
  id: string;
  name: string;
}
export interface Classification {
  categories: string[];
  industries: string[];
  importance: number;
  reasons: string[];
  rule_version: string;
}
export interface NewsEntry {
  id: number;
  source: string;
  source_id: string;
  title: string;
  content: string;
  url: string;
  published_at: number;
  first_seen: number;
  last_seen: number;
  source_observed_at: number;
  ingested_at: number;
  updated_at: number;
  version: number;
  baseline: boolean;
  classification: Classification | null;
}
export interface NewsPage {
  items: NewsEntry[];
  next: string;
  watermark: number;
  backlog: number;
}
export interface NewsMeta {
  categories: Topic[];
  industries: Topic[];
  sources: string[];
  rule_version: string;
  collection_enabled: boolean;
  trendradar_enabled: boolean;
  server_time: number;
  channels: {
    id: string;
    last_attempt: number;
    last_success: number;
    success: boolean;
    error: string;
    gap: string;
    gap_at: number;
  }[];
}
export interface NewsDetail {
  item: NewsEntry;
  revisions: {
    version: number;
    title: string;
    content: string;
    url: string | null;
    published_at: number | null;
    saved_at: number;
  }[];
  related: { item: NewsEntry; kind: string }[];
  revision_more: boolean;
  relation_scope: string;
}
const names: Record<string, string> = {
  wallstreetcn: '华尔街见闻快讯',
  'cls-depth': '财联社深度',
  'cls-hot': '财联社热门',
  'cls-telegraph': '财联社电报',
  'wallstreetcn-quick': '华尔街见闻快讯',
  cankaoxiaoxi: '参考消息',
  ifeng: '凤凰网',
  juejin: '掘金',
  v2ex: 'V2EX',
  zaobao: '联合早报',
};
export function sourceName(source: string) {
  return source.startsWith('trendradar:')
    ? `${names[source.slice(11)] ?? source.slice(11)} · TrendRadar`
    : (names[source] ?? source);
}
export function newsTime(value: number | null) {
  return value
    ? new Intl.DateTimeFormat('zh-CN', {
        timeZone: 'Asia/Shanghai',
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: false,
      }).format(new Date(value * 1000))
    : '未知';
}
export function firstSeenName(source: string) {
  return source.startsWith('trendradar:') ? '上游首次记录' : '首次发现';
}
export function safeNewsURL(raw: string | null) {
  try {
    const url = new URL(raw ?? '');
    return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password
      ? url.href
      : '';
  } catch {
    return '';
  }
}
export function topicNames(ids: string[] | undefined, labels: Topic[] | undefined) {
  return (ids ?? []).map((id) => labels?.find((label) => label.id === id)?.name ?? id).join(' · ');
}
export const importanceName: Record<number, string> = { 1: '一般', 2: '关注', 3: '重要' };
export const relationName: Record<string, string> = {
  same_url: '相同原文链接',
  same_content: '相同正文',
  similar_title: '相似标题 · 待核对',
};
