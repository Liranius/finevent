<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue';

import { api, ApiError, formatTime } from '../api';
import type { EventReminder } from '../reminders';

const props = defineProps<{ eventId: string; cancelled: boolean }>();
const emit = defineEmits<{ settings: []; expired: [] }>();
const state = ref<EventReminder | null>(null);
const error = ref('');
const notice = ref('');
const busy = ref(false);
let timer: ReturnType<typeof setInterval> | undefined;
let disposed = false;
async function load() {
  const next = await api<EventReminder>(`reminders/events/${props.eventId}`);
  if (!disposed) state.value = next;
}
async function action(work: () => Promise<void>) {
  busy.value = true;
  error.value = '';
  notice.value = '';
  try {
    await work();
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '关注操作失败';
    if (cause instanceof ApiError && cause.status === 401) emit('expired');
  } finally {
    busy.value = false;
  }
}
async function follow(inherit = false) {
  await action(async () => {
    await api(
      `reminders/events/${props.eventId}`,
      inherit ? 'DELETE' : 'PUT',
      inherit ? undefined : { enabled: !state.value?.followed },
    );
    await load();
    notice.value = inherit
      ? '已恢复类别和基金的关注规则。'
      : state.value?.followed
        ? '已关注此事件。'
        : '已关闭此事件的提醒。';
  });
}
onMounted(async () => {
  await action(load);
  if (disposed) return;
  timer = setInterval(() => {
    if (!busy.value && document.visibilityState === 'visible')
      void load().catch((cause: unknown) => {
        if (cause instanceof ApiError && cause.status === 401) emit('expired');
        else error.value = '提醒计划刷新失败，请重试。';
      });
  }, 10000);
});
onUnmounted(() => {
  disposed = true;
  clearInterval(timer);
});
</script>

<template>
  <section class="event-reminder" aria-label="此事件的提醒">
    <h3>此事件的提醒</h3>
    <p v-if="error" class="message error" role="alert">
      {{ error }}<button :disabled="busy" @click="action(load)">重试提醒状态</button>
    </p>
    <p v-if="notice" class="message" role="status">{{ notice }}</p>
    <template v-if="state">
      <div class="record-actions">
        <button :aria-pressed="state.followed" :disabled="busy" @click="follow()">
          {{ state.followed ? '取消关注此事件' : '关注此事件' }}
        </button>
        <button v-if="state.override !== null" class="quiet" :disabled="busy" @click="follow(true)">
          恢复默认关注规则
        </button>
        <button class="quiet" @click="emit('settings')">提醒设置</button>
      </div>
      <p v-if="cancelled" class="caption">事件已取消，尚未发送的提醒将撤销。</p>
      <p v-else-if="!state.enabled" class="caption">
        自动提醒尚未开启。关注会保留，请到提醒设置开启并选择接收设备。
      </p>
      <p v-else-if="!state.active_devices" class="caption">
        所选接收设备已失效，请到提醒设置重新选择。
      </p>
      <p v-else-if="!state.followed" class="caption">此事件未关注，不安排提醒。</p>
      <p v-else-if="!state.upcoming.length" class="caption">
        暂无待发送提醒；新改动约 10 秒内更新，已过的提醒时点不补发。
      </p>
      <ul v-else class="plain-list">
        <li v-for="item in state.upcoming" :key="item.id">
          {{ formatTime(item.scheduled_at) }} · 北京时间 · {{ item.label }}
        </li>
      </ul>
    </template>
    <p v-else-if="!error" class="caption">正在读取提醒状态…</p>
  </section>
</template>
