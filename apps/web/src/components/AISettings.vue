<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, reactive, ref } from 'vue';

import { aiState, yuan, type AIJob, type AIOverview } from '../ai';
import { api, ApiError } from '../api';
import { newsTime } from '../news';
import AnalysisResult from './AnalysisResult.vue';
defineProps<{ recordsOnly?: boolean }>();
const emit = defineEmits<{ expired: [] }>();
const info = ref<AIOverview | null>(null),
  selected = ref<AIJob | null>(null);
const form = reactive({ enabled: false, automatic: false });
const dirty = ref(false),
  busy = ref(false),
  error = ref(''),
  notice = ref('');
let disposed = false,
  refreshing = false,
  selectionRequest = 0,
  refreshRequest = 0;
let timer: ReturnType<typeof setInterval> | undefined;
function failure(cause: unknown) {
  if (cause instanceof ApiError && cause.status === 401) emit('expired');
  return cause instanceof Error ? cause.message : '解读设置暂时不可用';
}
async function refresh(force = false) {
  if ((refreshing && !force) || disposed) return;
  const request = ++refreshRequest;
  if (force) error.value = '';
  refreshing = true;
  try {
    const next = await api<AIOverview>('ai');
    if (!disposed && request === refreshRequest) {
      info.value = next;
      if (!dirty.value) Object.assign(form, next.settings);
    }
  } catch (cause) {
    if (!disposed && request === refreshRequest) error.value = failure(cause);
  } finally {
    if (request === refreshRequest) refreshing = false;
  }
}
async function save() {
  ++refreshRequest;
  refreshing = false;
  busy.value = true;
  error.value = '';
  notice.value = '';
  try {
    await api('ai/settings', 'PUT', form);
    if (!disposed) {
      dirty.value = false;
      notice.value = '解读设置已保存。';
    }
    await refresh(true);
  } catch (cause) {
    if (!disposed) error.value = failure(cause);
  } finally {
    if (!disposed) busy.value = false;
  }
}
async function open(id: string) {
  const request = ++selectionRequest;
  error.value = '';
  selected.value = null;
  try {
    const next = await api<AIJob>(`ai/analyses/${id}`);
    if (!disposed && request === selectionRequest) {
      selected.value = next;
      await nextTick();
      document.getElementById('ai-history-heading')?.focus();
    }
  } catch (cause) {
    if (!disposed && request === selectionRequest) error.value = failure(cause);
  }
}
function visible() {
  if (!busy.value && document.visibilityState === 'visible') void refresh();
}
onMounted(() => {
  document.getElementById('ai-heading')?.focus();
  void refresh();
  timer = setInterval(visible, 30000);
  document.addEventListener('visibilitychange', visible);
});
onUnmounted(() => {
  disposed = true;
  ++selectionRequest;
  clearInterval(timer);
  document.removeEventListener('visibilitychange', visible);
});
</script>

<template>
  <section class="ai-workspace" :aria-label="recordsOnly ? 'AI 解读记录' : 'AI 设置与预算'">
    <div class="page-heading">
      <div>
        <h1 id="ai-heading" tabindex="-1">{{ recordsOnly ? 'AI 解读记录' : 'AI 设置与预算' }}</h1>
      </div>
      <button :disabled="busy" @click="refresh(true)">
        {{ recordsOnly ? '刷新解读记录' : '刷新用量' }}
      </button>
    </div>
    <p v-if="error" class="message error" role="alert">{{ error }}</p>
    <p v-if="notice" class="message" role="status">{{ notice }}</p>
    <p v-if="!info && !error" role="status">
      {{ recordsOnly ? '正在读取解读记录…' : '正在读取设置与预算…' }}
    </p>
    <div v-if="info && !recordsOnly" class="ai-settings-grid">
      <form @submit.prevent="save" @input="dirty = true" @change="dirty = true">
        <h2>解读范围</h2>
        <p v-if="!info.configured" class="message">
          服务端密钥或价格配置未就绪。请在服务器终端配置；此页面不接收密钥。
        </p>
        <label class="checkbox-label"
          ><input
            v-model="form.enabled"
            type="checkbox"
            :disabled="busy || (!info.configured && !form.enabled)"
          />启用 AI 解读</label
        >
        <label class="checkbox-label"
          ><input
            v-model="form.automatic"
            type="checkbox"
            :disabled="busy"
          />自动解读新的重要报道</label
        >
        <p class="caption">
          仅处理开启后新入库、规则标为“重要”且非历史补采的报道。其他报道在详情页手动请求；暂停后保留队列，正在进行的请求可能完成。
        </p>
        <button class="primary" :disabled="busy">{{ busy ? '正在保存…' : '保存解读设置' }}</button>
        <p v-if="dirty" class="caption">有未保存的设置。</p>
        <p class="caption">
          {{ info.model || '未配置模型' }} · 价格版本 {{ info.price_version || '未配置'
          }}<br />价格复核截止 {{ info.prices_valid_until || '未配置' }}；到期暂停调用。
        </p>
      </form>
      <aside aria-label="AI 月预算" class="ai-budget">
        <span class="eyebrow">{{ info.month }} · 北京时间</span>
        <h2>本月可用 {{ yuan(info.remaining_nano) }}</h2>
        <dl>
          <dt>月预算</dt>
          <dd>{{ yuan(info.budget_nano) }}</dd>
          <dt>已知用量估算</dt>
          <dd>{{ yuan(info.estimated_nano) }}</dd>
          <dt>调用预留 / 用量未结</dt>
          <dd>{{ yuan(info.reserved_nano) }}</dd>
          <dt>其中：历史探测占用</dt>
          <dd>{{ yuan(info.probe_nano) }}</dd>
          <dt>等待与进行中的任务</dt>
          <dd>{{ info.queued }}</dd>
        </dl>
        <p class="caption">
          按配置价格及返回用量估算，含探测费用；不是账户账单。无法确认费用时保留预留，不自动重复调用。
        </p>
        <p v-if="info.not_before > Date.now() / 1000" class="message">
          模型调用暂缓至 {{ newsTime(info.not_before) }}，采集继续运行。
        </p>
      </aside>
    </div>
    <section v-if="info && recordsOnly" class="ai-history" aria-label="最近解读任务">
      <h2>最近解读记录</h2>
      <p v-if="!info.recent.length">尚无解读任务。启用后可在新闻详情请求。</p>
      <ul class="plain-list">
        <li v-for="item in info.recent" :key="item.id">
          <span class="caption"
            >{{ newsTime(item.created) }} · {{ aiState[item.status] ?? item.status }}</span
          ><button class="news-related-link" @click="open(item.id)">
            {{ item.title || '查看解读记录' }}
          </button>
          <p v-if="item.error" class="caption">{{ item.error }}</p>
        </li>
      </ul>
      <p v-if="info.recent.length" class="caption">列出最近 20 项。解读及输入快照保留一年。</p>
    </section>
    <section v-if="recordsOnly && selected" class="ai-history-detail" aria-label="历史解读内容">
      <h2 id="ai-history-heading" tabindex="-1">{{ selected.evidence[0]?.title || '解读记录' }}</h2>
      <AnalysisResult :job="selected" />
    </section>
  </section>
</template>
