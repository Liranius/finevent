<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, useId } from 'vue';

import { api, ApiError } from '../api';
import { newsTime, sourceName } from '../news';
import { sourceStateName, type TrendHealth, type TrendFailurePage } from '../source-health';

const emit = defineEmits<{ expired: [] }>();
const id = useId();
const health = ref<TrendHealth | null>(null);
const busy = ref(false);
const error = ref('');
const pollError = ref('');
const history = ref<TrendFailurePage | null>(null);
const historyOpen = ref(false);
const historyBusy = ref(false);
const historyError = ref('');
const source = ref('');
const historyHeading = ref<HTMLElement | null>(null);
const attention = computed(
  () => health.value?.sources.filter((s) => s.state !== 'healthy').length ?? 0,
);
let disposed = false;
let historyRequest = 0;
let timer: ReturnType<typeof setInterval> | undefined;
function message(cause: unknown) {
  if (cause instanceof ApiError && cause.status === 401) emit('expired');
  return cause instanceof Error ? cause.message : '来源状态暂时无法读取。';
}
async function refresh(manual = true) {
  if (busy.value || disposed) return;
  busy.value = true;
  if (manual) error.value = '';
  try {
    const result = await api<TrendHealth>('news/source-health');
    if (disposed) return;
    health.value = result;
    pollError.value = '';
  } catch (cause) {
    if (disposed) return;
    if (manual) error.value = message(cause);
    else pollError.value = message(cause);
  } finally {
    if (!disposed) busy.value = false;
  }
}
async function loadHistory(more = false) {
  const request = ++historyRequest;
  historyBusy.value = true;
  historyError.value = '';
  const query = new URLSearchParams({ source: source.value });
  if (more && history.value?.next) query.set('before', history.value.next);
  if (!more) history.value = null;
  try {
    const result = await api<TrendFailurePage>(`news/source-failures?${query}`);
    if (disposed || request !== historyRequest) return;
    if (more && history.value) {
      const existing = new Set(history.value.items.map((row) => row.id));
      history.value = {
        ...result,
        items: [...history.value.items, ...result.items.filter((row) => !existing.has(row.id))],
      };
    } else history.value = result;
  } catch (cause) {
    if (!disposed && request === historyRequest) historyError.value = message(cause);
  } finally {
    if (!disposed && request === historyRequest) historyBusy.value = false;
  }
}
async function showHistory(value: string) {
  source.value = value;
  historyOpen.value = true;
  await loadHistory();
  if (!disposed) {
    await nextTick();
    historyHeading.value?.focus();
  }
}
function toggleHistory(event: Event) {
  historyOpen.value = (event.target as HTMLDetailsElement).open;
  if (historyOpen.value && !history.value && !historyBusy.value) void loadHistory();
}
function onVisible() {
  if (document.visibilityState === 'visible') void refresh(false);
}
onMounted(() => {
  void refresh();
  timer = setInterval(onVisible, 30000);
  document.addEventListener('visibilitychange', onVisible);
});
onUnmounted(() => {
  disposed = true;
  ++historyRequest;
  clearInterval(timer);
  document.removeEventListener('visibilitychange', onVisible);
});
</script>

<template>
  <section class="source-health" :aria-labelledby="`${id}-heading`" :aria-busy="busy">
    <div class="section-title">
      <h2 :id="`${id}-heading`">TrendRadar 来源健康</h2>
      <button class="quiet" :disabled="busy" @click="refresh()">
        {{ busy ? '读取中…' : '刷新来源状态' }}
      </button>
    </div>
    <p class="caption">
      这里核对上游各来源的采集结果。数据库导入成功不代表新闻源正常；上游约每 30 分钟采集。
    </p>
    <p v-if="error || pollError" role="alert" class="error">
      {{ error || pollError }}<span v-if="health"> 以下保留上次读取结果。</span>
    </p>
    <p v-if="!health && !error && !pollError" class="empty" role="status">正在读取来源记录…</p>
    <template v-if="health">
      <p v-if="!health.collection_enabled || !health.trendradar_enabled" class="message">
        来源状态采样未启用；已有记录仅供回看。
      </p>
      <p v-if="health.read_error" class="message error">
        {{ health.read_error }} 保留已有记录，当前健康无法确认。
      </p>
      <p v-else-if="health.read_stale && health.read_attempt" class="message warning">
        状态读取已超过 3 分钟未成功，当前健康需核查。
      </p>
      <div class="source-health-overview">
        <strong :class="{ warning: attention > 0 }">{{
          health.sources.length
            ? `${health.sources.length} 个已观察来源 · ${attention} 项需核查`
            : '尚无来源状态记录'
        }}</strong>
        <span>最新采集轮次 {{ newsTime(health.latest_cycle) }}</span>
        <span>状态读取成功 {{ newsTime(health.read_success) }}</span>
      </div>
      <p v-for="warning in health.warnings" :key="warning" class="caption warning">{{ warning }}</p>
      <p v-if="!health.sources.length" class="empty">
        等待有来源明细的采集轮次；没有记录不表示采集正常。
      </p>
      <div v-else class="source-health-list">
        <details v-for="item in health.sources" :key="item.id" class="source-health-row">
          <summary>
            <strong>{{ sourceName(item.id) }}</strong>
            <span
              :class="
                item.state === 'failed'
                  ? 'error'
                  : item.state === 'healthy'
                    ? 'source-healthy'
                    : 'warning'
              "
              >{{ sourceStateName[item.state] }}</span
            >
            <time>{{ newsTime(item.last_attempt) }}</time>
          </summary>
          <div class="source-health-detail">
            <p v-if="item.state === 'missing'" class="warning">
              最新一轮没有该来源的状态，不能推断成功、失败或已停采。
            </p>
            <p v-if="item.stale" class="warning">超过 40 分钟没有新的来源记录。</p>
            <dl class="source-health-times">
              <div>
                <dt>最近成功</dt>
                <dd>{{ newsTime(item.last_success) }}</dd>
              </div>
              <div>
                <dt>最近失败</dt>
                <dd>{{ item.last_failure ? newsTime(item.last_failure) : '保留记录内未见' }}</dd>
              </div>
              <div>
                <dt>该次失败后首次成功</dt>
                <dd>
                  {{
                    item.recovered_at
                      ? newsTime(item.recovered_at)
                      : item.last_failure
                        ? '尚未观察到'
                        : '—'
                  }}
                </dd>
              </div>
            </dl>
            <button class="quiet" @click="showHistory(item.id)">
              查看{{ sourceName(item.id) }}故障记录
            </button>
          </div>
        </details>
      </div>
      <details class="source-health-history" :open="historyOpen" @toggle="toggleHistory">
        <summary>故障与恢复记录</summary>
        <h3 ref="historyHeading" tabindex="-1">采集故障历史</h3>
        <p class="caption">
          记录保留 30 天；当前最早轮次
          {{
            newsTime(health.history_since)
          }}。首次接入读取当天和前一天，未覆盖时段不补造。恢复时间是之后首次成功的采集轮次，不代表精确停机时长或漏采已补齐。
        </p>
        <form class="source-history-filter" @submit.prevent="loadHistory()">
          <label :for="`${id}-filter`">故障来源</label>
          <select :id="`${id}-filter`" v-model="source" :disabled="historyBusy">
            <option value="">全部已观察来源</option>
            <option v-for="item in health.sources" :key="item.id" :value="item.id">
              {{ sourceName(item.id) }}
            </option>
          </select>
          <button :disabled="historyBusy">查询故障记录</button>
        </form>
        <p v-if="historyError" role="alert" class="error">{{ historyError }} 请重新查询。</p>
        <p v-if="historyBusy" role="status">正在读取故障历史…</p>
        <p v-else-if="history && !history.items.length" class="empty">
          当前保留范围内未见该筛选的失败记录；这不证明全程无缺口。
        </p>
        <ol v-if="history?.items.length" class="plain-list source-failure-list">
          <li v-for="row in history.items" :key="row.id">
            <div class="record-heading">
              <strong>{{ sourceName(row.source) }}</strong
              ><span :class="row.status === 'failed' ? 'error' : 'warning'"
                >{{ row.status === 'failed' ? '采集失败' : '来源状态未知' }} ·
                {{ newsTime(row.at) }}</span
              >
            </div>
            <p>之后首次成功：{{ row.recovered_at ? newsTime(row.recovered_at) : '尚未观察到' }}</p>
            <p v-if="row.corrected" class="caption warning">
              上游已修订或移除此轮状态，保留最初观察到的异常；同轮修订不算后续轮次恢复。
            </p>
          </li>
        </ol>
        <button v-if="history?.next" :disabled="historyBusy" @click="loadHistory(true)">
          加载更早故障
        </button>
      </details>
    </template>
  </section>
</template>
