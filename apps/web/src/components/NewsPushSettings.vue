<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue';
import { RouterLink, useRoute } from 'vue-router';

import { api, ApiError, formatTime } from '../api';
import type { NewsPushData, NewsPushSettings } from '../news-push';
import { reminderState } from '../reminders';

const props = defineProps<{ recordsOnly: boolean }>();
const route = useRoute();
const emit = defineEmits<{ expired: []; devices: [] }>();
const data = ref<NewsPushData | null>(null);
const form = ref<NewsPushSettings | null>(null);
const busy = ref(false);
const error = ref('');
const refreshError = ref('');
const notice = ref('');
let disposed = false;
let readRequest = 0;
let refreshing = false;
let timer: ReturnType<typeof setInterval> | undefined;
function message(cause: unknown) {
  if (cause instanceof ApiError && cause.status === 401) emit('expired');
  return cause instanceof Error ? cause.message : '新闻推送设置暂时无法读取。';
}
async function load(reset = false) {
  const request = ++readRequest;
  const next = await api<NewsPushData>('news-push');
  if (disposed || request !== readRequest) return;
  data.value = next;
  if (reset || !form.value) form.value = structuredClone(next.settings);
}
async function refresh() {
  if (disposed || busy.value || refreshing || document.visibilityState !== 'visible') return;
  refreshing = true;
  try {
    await load();
    if (!disposed) refreshError.value = '';
  } catch (cause) {
    if (!disposed) refreshError.value = message(cause);
  } finally {
    refreshing = false;
  }
}
async function save() {
  if (!form.value || busy.value) return;
  busy.value = true;
  error.value = '';
  notice.value = '';
  try {
    await api('news-push/settings', 'PUT', form.value);
    await load(true);
    notice.value = form.value.enabled
      ? '新闻推送已开启。只提醒此后新到达、符合关注范围的重要新闻。'
      : '新闻推送已暂停，尚未发送的任务已撤销。';
  } catch (cause) {
    if (!disposed) error.value = message(cause);
  } finally {
    busy.value = false;
  }
}
watch(
  () => props.recordsOnly,
  () => void refresh(),
);
function onVisible() {
  void refresh();
}
onMounted(() => {
  void refresh();
  timer = setInterval(() => void refresh(), 30000);
  document.addEventListener('visibilitychange', onVisible);
});
onUnmounted(() => {
  disposed = true;
  clearInterval(timer);
  document.removeEventListener('visibilitychange', onVisible);
});
</script>

<template>
  <div>
    <p v-if="error && !recordsOnly" class="message error" role="alert">{{ error }}</p>
    <p v-if="refreshError" class="message error" role="alert">
      {{ refreshError }} <button @click="refresh">重试读取推送记录</button>
    </p>
    <p v-if="notice && !recordsOnly" class="message" role="status">{{ notice }}</p>
    <p v-if="!data && !refreshError" class="empty" role="status">
      {{ recordsOnly ? '正在读取通知记录…' : '正在读取推送设置…' }}
    </p>
    <div v-if="!recordsOnly && data && form" class="entry-layout">
      <form class="primary-panel reminder-form" aria-label="新闻推送设置" @submit.prevent="save">
        <span class="eyebrow">FOLLOW THE IMPORTANT</span>
        <h2>只提醒关注的重要变化</h2>
        <p class="caption">类别或行业命中任一项即可；仅推送规则重要性为“重要”的报道。</p>
        <label class="check-row">
          <input v-model="form.enabled" type="checkbox" :disabled="busy" />开启新闻推送
        </label>
        <fieldset :disabled="busy">
          <legend>接收设备</legend>
          <p v-if="!data.devices.length" class="caption">还没有设备，先连接通知再回来选择。</p>
          <label v-for="device in data.devices" :key="device.id" class="check-row">
            <input
              v-model="form.devices"
              type="checkbox"
              :value="device.id"
              :disabled="!device.active && !form.devices.includes(device.id)"
            />
            {{ device.label }}{{ device.active ? '' : ' · 已停用，请取消选择' }}
          </label>
          <button type="button" @click="emit('devices')">连接或管理通知设备</button>
        </fieldset>
        <fieldset :disabled="busy">
          <legend>关注类别</legend>
          <div class="news-push-options">
            <label v-for="tag in data.categories" :key="tag.id" class="check-row">
              <input v-model="form.categories" type="checkbox" :value="tag.id" />{{ tag.name }}
            </label>
          </div>
        </fieldset>
        <fieldset :disabled="busy">
          <legend>关注行业</legend>
          <div class="news-push-options">
            <label v-for="tag in data.industries" :key="tag.id" class="check-row">
              <input v-model="form.industries" type="checkbox" :value="tag.id" />{{ tag.name }}
            </label>
          </div>
        </fieldset>
        <button class="primary" :disabled="busy">
          {{ busy ? '正在保存…' : '保存新闻推送设置' }}
        </button>
        <p class="caption">更改关注或设备会撤销待发任务，从保存后继续；已发出的通知无法撤回。</p>
      </form>
      <aside class="evidence-panel reminder-agenda" aria-label="新闻推送规则与状态">
        <span class="eyebrow">DELIVERY RECORD</span>
        <h2>{{ data.settings.enabled ? '正在关注新消息' : '新闻推送已暂停' }}</h2>
        <p>
          已保存 {{ data.settings.categories.length }} 个类别、{{
            data.settings.industries.length
          }}
          个行业，选择 {{ data.settings.devices.length }} 台设备。
        </p>
        <p v-if="data.backlog">待检查 {{ data.backlog }} 条新增或修订记录。</p>
        <dl class="news-times">
          <dt>时效</dt>
          <dd>仅处理近 30 分钟的新消息。入队后 15 分钟过期，不补发过期通知。</dd>
          <dt>重复报道</dt>
          <dd>24 小时内原文链接或完整正文一致的报道合并；相似标题保留为独立报道。</dd>
          <dt>再次提醒</dt>
          <dd>同一来源条目仍为“重要”，且关键数字或状态词变化时再次提醒。文字润色不重复推送。</dd>
          <dt>依据</dt>
          <dd>通知展示来源事实与规则重要性，不把分类当作投资方向，也不额外调用 AI。</dd>
        </dl>
        <p class="caption">
          来源发布时间未知会明确标记。推送服务接受不等于锁屏显示，设备回执与点击另行记录。
        </p>
        <div class="record-actions">
          <button :disabled="busy" @click="refresh">刷新推送状态</button>
          <RouterLink :to="{ name: 'news-notifications' }">查看报道通知记录</RouterLink>
        </div>
      </aside>
    </div>
    <section
      v-if="data && recordsOnly"
      class="reminder-history news-notification-history"
      aria-label="报道通知记录"
    >
      <div class="agenda-heading">
        <h2>最近通知</h2>
        <button :disabled="busy" @click="refresh">刷新推送记录</button>
      </div>
      <p v-if="!data.history.length" class="empty">
        尚无报道通知。开启推送后，符合关注条件的新报道会在这里留下记录。
      </p>
      <ul v-else class="plain-list">
        <li v-for="item in data.history" :key="item.id">
          <div class="record-heading">
            <strong>{{ reminderState(item) }}</strong
            ><time>{{ formatTime(item.created) }}</time>
          </div>
          <RouterLink
            v-if="item.news_id"
            class="reminder-event-link"
            :to="{
              name: 'news-detail',
              params: { id: item.news_id },
              state: { linkedFrom: route.fullPath },
            }"
            >{{ item.title }}</RouterLink
          >
          <strong v-else>{{ item.title }} · 原报道已按保留期清理</strong>
          <p>{{ item.label }} · {{ item.reason }} · 第 {{ item.version }} 版</p>
          <p class="caption">{{ item.body }}</p>
          <p v-if="item.accepted_at" class="caption">
            推送服务接受：{{ formatTime(item.accepted_at) }}
          </p>
          <p v-if="item.received_at" class="caption">
            设备回执：{{ formatTime(item.received_at) }}
          </p>
          <p v-if="item.clicked_at" class="caption">点击：{{ formatTime(item.clicked_at) }}</p>
          <p v-if="item.error" class="news-warning">{{ item.error }}</p>
        </li>
      </ul>
    </section>
  </div>
</template>
