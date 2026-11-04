export interface AISettings {
  enabled: boolean;
  automatic: boolean;
}
export interface AIEvidence {
  id: string;
  news_id: number;
  version: number;
  source: string;
  url: string;
  title: string;
  text: string;
  published_at: number;
  truncated: boolean;
}
export interface AIValue {
  value: string;
  evidence: string;
  quote: string;
}
export interface AIJob {
  id: string;
  model: string;
  prompt_version: string;
  evidence: AIEvidence[];
  status: string;
  created: number;
  completed: number;
  expires: number;
  error: string;
  attempts: number;
  stale: boolean;
  calls: {
    started: number;
    status: string;
    reserved_nano: number;
    estimated_nano: number | null;
    prompt_tokens: number | null;
    completion_tokens: number | null;
    price_version: string;
    price_basis: string;
  }[];
  result: {
    facts: { text: string; evidence: string; quote: string }[];
    categories: string[];
    impacts: {
      target: string;
      market: string;
      industry: string;
      direction: string;
      path: string;
      conditions: string[];
      horizon: string;
      counterfactors: string[];
      evidence: string[];
    }[];
    metrics: {
      name: string;
      actual: AIValue | null;
      expected: AIValue | null;
      previous: AIValue | null;
    }[];
    uncertainty: string;
  } | null;
}
export interface AIOverview {
  settings: AISettings;
  configured: boolean;
  model: string;
  price_version: string;
  prices_valid_until: string;
  month: string;
  budget_nano: number;
  estimated_nano: number;
  reserved_nano: number;
  probe_nano: number;
  remaining_nano: number;
  queued: number;
  not_before: number;
  recent: {
    id: string;
    status: string;
    created: number;
    error: string;
    attempts: number;
    title: string;
  }[];
}
export const aiState: Record<string, string> = {
  config_changed: '模型配置已变化',
  superseded: '来源已修订',
  queued: '等待解读',
  running: '正在解读',
  succeeded: '解读已完成',
  budget_wait: '预算不足，等待下月',
  uncertain: '上次调用中断，待核对',
  http_error: '模型服务暂不可用',
  request_failed: '调用未完成',
  invalid_output: '解读未通过校验',
  usage_unknown: '用量未确认',
  reservation_exceeded: '用量异常，AI 已暂停',
};
export const directionName: Record<string, string> = {
  positive: '利好',
  negative: '利空',
  mixed: '混合',
  uncertain: '不确定',
};
export function yuan(nano: number) {
  return `¥${(nano / 1e9).toFixed(4)}`;
}
export function canRetry(job: AIJob) {
  return (
    job.attempts < 3 &&
    [
      'uncertain',
      'http_error',
      'request_failed',
      'invalid_output',
      'usage_unknown',
      'reservation_exceeded',
    ].includes(job.status)
  );
}
