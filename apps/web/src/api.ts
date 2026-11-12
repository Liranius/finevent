export interface Device {
  id: number;
  label: string;
  active: number;
}
export interface Delivery {
  id: number;
  label: string;
  scenario: string;
  status: string;
  due: number;
  accepted_at: number;
  received_at: number;
  clicked_at: number;
  error: string;
  manual_result: string;
}
export interface Poll {
  id: number;
  source: string;
  started: number;
  completed: number;
  success: number;
  added: number;
  changed: number;
  gap: string;
  error: string;
}
export interface News {
  id: number;
  source: string;
  title: string;
  url: string;
  published_at: number;
  first_seen: number;
  baseline: number;
}
export interface Resource {
  at: number;
  rss: number;
  go_sys: number;
  available: number;
  cgroup_current: number;
  cgroup_peak: number;
  oom_kills: number;
}
export interface Latency {
  source: string;
  samples: number;
  p50_seconds: number;
  p95_seconds: number;
  within_5_minutes: number;
  unknown_publication: number;
  clock_errors: number;
}
export interface Status {
  vapid_public_key: string;
  collection_enabled: boolean;
  trendradar_enabled: boolean;
  server_time: number;
  devices: Device[];
  deliveries: Delivery[];
  polls: Poll[];
  news: News[];
  resources: Resource[];
  latencies: Latency[];
  coverage: {
    source: string;
    first_poll: number;
    last_poll: number;
    polls: number;
    successes: number;
    gaps: number;
  }[];
}

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}
export async function api<T>(path: string, method = 'GET', data?: unknown): Promise<T> {
  const options: RequestInit = {
    method,
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    signal: AbortSignal.timeout(15000),
  };
  if (data !== undefined && method !== 'GET' && method !== 'HEAD')
    options.body = JSON.stringify(data);
  const response = await fetch(`/finevent/api/v1/${path}`, options);
  const body = (await response.json()) as T & { error?: string };
  if (!response.ok) throw new ApiError(body.error ?? '请求失败，请稍后重试', response.status);
  return body;
}
export function formatTime(seconds: number): string {
  return seconds
    ? new Intl.DateTimeFormat('zh-CN', {
        timeZone: 'Asia/Shanghai',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: false,
      }).format(seconds * 1000)
    : '尚无记录';
}
export function deliveryState(item: Delivery): string {
  if (item.clicked_at) return '已点击';
  if (item.received_at) return '设备已收到';
  return (
    (
      {
        queued: '等待发送',
        sending: '正在发送',
        accepted: '服务已接受',
        failed: '发送失败',
        expired: '已过期',
      } as Record<string, string>
    )[item.status] ?? item.status
  );
}
export function vapidBytes(key: string): Uint8Array<ArrayBuffer> {
  return Uint8Array.from(atob(key.replace(/-/g, '+').replace(/_/g, '/')), (char) =>
    char.charCodeAt(0),
  );
}
