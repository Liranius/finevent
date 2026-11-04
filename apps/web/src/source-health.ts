export interface TrendSourceHealth {
  id: string;
  state: 'healthy' | 'failed' | 'missing' | 'unknown' | 'stale';
  last_attempt: number;
  last_status: string;
  last_success: number;
  last_failure: number;
  recovered_at: number;
  stale: boolean;
}
export interface TrendHealth {
  server_time: number;
  collection_enabled: boolean;
  trendradar_enabled: boolean;
  read_attempt: number;
  read_success: number;
  read_error: string;
  read_stale: boolean;
  warnings: string[];
  latest_cycle: number;
  history_since: number;
  sources: TrendSourceHealth[];
}
export interface TrendFailurePage {
  items: {
    id: number;
    source: string;
    at: number;
    status: string;
    recovered_at: number;
    corrected: boolean;
  }[];
  next: string;
}
export const sourceStateName: Record<TrendSourceHealth['state'], string> = {
  healthy: '最近采集成功',
  failed: '最近采集失败',
  missing: '最新一轮缺记录',
  unknown: '状态待核查',
  stale: '采集记录已过期',
};
