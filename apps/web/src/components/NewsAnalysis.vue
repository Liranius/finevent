<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue';

import { canRetry, type AIJob, type AIOverview } from '../ai';
import { api, ApiError } from '../api';
import AnalysisResult from './AnalysisResult.vue';
const props = defineProps<{ newsId: number }>();
const emit = defineEmits<{ expired: [] }>();
const job = ref<AIJob | null>(null);
const info = ref<AIOverview | null>(null);
const needsUpdate = computed(
  () =>
    job.value &&
    (job.value.stale ||
      job.value.model !== info.value?.model ||
      ['superseded', 'config_changed'].includes(job.value.status)),
);
const loaded = ref(false),
  busy = ref(false),
  error = ref('');
let disposed = false,
  refreshing = false,
  refreshRequest = 0;
let timer: ReturnType<typeof setInterval> | undefined;
function failure(cause: unknown) {
  if (cause instanceof ApiError && cause.status === 401) emit('expired');
  return cause instanceof Error ? cause.message : '解读暂时无法读取';
}
async function refresh(manual = false) {
  if ((refreshing && !manual) || disposed) return;
  const request = ++refreshRequest;
  refreshing = true;
  if (manual) error.value = '';
  try {
    const [detail, status] = await Promise.all([
      api<{ job: AIJob | null }>(`news/${props.newsId}/analysis`),
      api<AIOverview>('ai'),
    ]);
    if (!disposed && request === refreshRequest) {
      job.value = detail.job;
      info.value = status;
      loaded.value = true;
    }
  } catch (cause) {
    if (!disposed && request === refreshRequest) error.value = failure(cause);
  } finally {
    if (request === refreshRequest) refreshing = false;
  }
}
async function request() {
  busy.value = true;
  error.value = '';
  try {
    await api(`news/${props.newsId}/analysis`, 'POST', {
      retry: Boolean(job.value && !needsUpdate.value && canRetry(job.value)),
    });
    await refresh(true);
  } catch (cause) {
    if (!disposed) error.value = failure(cause);
  } finally {
    if (!disposed) busy.value = false;
  }
}
function visible() {
  if (document.visibilityState === 'visible') void refresh();
}
onMounted(() => {
  void refresh();
  timer = setInterval(visible, 30000);
  document.addEventListener('visibilitychange', visible);
});
onUnmounted(() => {
  disposed = true;
  clearInterval(timer);
  document.removeEventListener('visibilitychange', visible);
});
</script>

<template>
  <section class="news-analysis" aria-label="事件影响解读">
    <div class="ai-section-heading">
      <h3>事件影响解读</h3>
      <button :disabled="busy" @click="refresh(true)">刷新解读</button>
    </div>
    <p v-if="error" class="message error" role="alert">{{ error }}</p>
    <p v-if="!loaded && !error" role="status">正在读取解读状态…</p>
    <p v-if="loaded && !job">这条报道尚无解读。可根据来源请求一次分析。</p>
    <AnalysisResult v-if="job" :job="job" />
    <template v-if="loaded">
      <p v-if="!info?.configured || !info.settings.enabled" class="caption">
        AI 尚未启用；可前往设置中的“AI 解读”查看开关，新闻原文可正常阅读。
      </p>
      <template v-else-if="!job || needsUpdate || canRetry(job)">
        <p v-if="job && !needsUpdate && canRetry(job)" class="caption">
          重试可能产生新费用，原调用的未结预留仍计入预算；每份内容最多尝试 3 次。
        </p>
        <button :disabled="busy" @click="request">
          {{
            busy
              ? '正在安排…'
              : needsUpdate
                ? '解读最新版本'
                : job && canRetry(job)
                  ? '重试解读'
                  : '请求事件解读'
          }}
        </button>
      </template>
    </template>
  </section>
</template>
