<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue';

import { api, ApiError, formatTime } from '../api';
import { reminderState, type ReminderData, type ReminderSettings } from '../reminders';

const emit = defineEmits<{ expired: []; devices: []; event: [id: string] }>();
const data = ref<ReminderData | null>(null);
const form = ref<ReminderSettings | null>(null);
const codes = ref('');
const busy = ref(false);
const error = ref('');
const notice = ref('');
let timer: ReturnType<typeof setInterval> | undefined;
let disposed = false;
async function load(reset = false) {
  const next = await api<ReminderData>('reminders');
  if (disposed) return;
  data.value = next;
  if (!form.value || reset) {
    form.value = structuredClone(next.settings);
    codes.value = next.settings.fund_codes.join('、');
  }
}
async function action(work: () => Promise<void>) {
  busy.value = true;
  error.value = '';
  notice.value = '';
  try {
    await work();
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '提醒设置读取失败';
    if (cause instanceof ApiError && cause.status === 401) emit('expired');
  } finally {
    busy.value = false;
  }
}
async function save() {
  await action(async () => {
    if (!form.value) return;
    const fundCodes = [...new Set(codes.value.split(/[\s,，、]+/).filter(Boolean))];
    if (fundCodes.some((code) => !/^\d{6}$/.test(code)))
      throw new Error('请填写六位基金代码，用逗号或空格分隔。');
    await api('reminders/settings', 'PUT', { ...form.value, fund_codes: fundCodes });
    await load(true);
    notice.value = form.value.enabled
      ? '自动提醒已开启，待发送计划已更新。'
      : '自动提醒已暂停，尚未发送的任务已撤销。';
  });
}
onMounted(async () => {
  await action(() => load());
  if (disposed) return;
  timer = setInterval(() => {
    if (!busy.value && document.visibilityState === 'visible')
      void load().catch((cause: unknown) => {
        if (cause instanceof ApiError && cause.status === 401) emit('expired');
        else error.value = '提醒记录刷新失败，保留当前设置。';
      });
  }, 30000);
});
onUnmounted(() => {
  disposed = true;
  clearInterval(timer);
});
</script>

<template>
  <section class="reminder-workspace" aria-label="日历提醒设置">
    <p v-if="error" class="message error" role="alert">{{ error }}</p>
    <p v-if="notice" class="message" role="status">{{ notice }}</p>
    <p v-if="!form" class="empty">
      {{ busy ? '正在读取提醒设置…' : '暂时无法读取提醒设置。'
      }}<button v-if="!busy" @click="action(() => load())">重试提醒设置</button>
    </p>
    <template v-else-if="data">
      <div class="entry-layout">
        <form class="primary-panel reminder-form" @submit.prevent="save">
          <h2>把关注变成提醒</h2>
          <label class="check-row"
            ><input v-model="form.enabled" type="checkbox" />开启日历自动提醒</label
          >
          <p class="caption">
            按已入历的日程发送；预定时间仍可能调整。改期或取消后，尚未发送的提醒会重新计算。
          </p>
          <fieldset>
            <legend>接收设备</legend>
            <p v-if="!data.devices.some((device) => device.active)" class="caption">
              暂无可用设备，请先连接浏览器通知。
            </p>
            <label v-for="device in data.devices" :key="device.id" class="check-row">
              <input
                v-model="form.devices"
                type="checkbox"
                :value="device.id"
                :disabled="!device.active && !form.devices.includes(device.id)"
              />{{ device.label }}{{ device.active ? '' : '（已停用）' }}
            </label>
            <button type="button" class="quiet" @click="emit('devices')">管理接收设备</button>
          </fieldset>
          <fieldset>
            <legend>持续关注</legend>
            <label class="check-row"
              ><input v-model="form.kinds" type="checkbox" value="macro" />宏观经济日历</label
            >
            <label class="check-row"
              ><input v-model="form.kinds" type="checkbox" value="monetary" />货币政策日历</label
            >
            <label class="check-row"
              ><input v-model="form.kinds" type="checkbox" value="other" />手工补录事件</label
            >
            <label for="reminder-funds">关注基金代码</label>
            <input
              id="reminder-funds"
              v-model="codes"
              type="text"
              placeholder="例如：123456、654321"
            />
            <p class="caption">
              仅提醒已确认入历的公告，不推算下一开放周期。也可在事件详情中单独关注。
            </p>
          </fieldset>
          <fieldset>
            <legend>有具体时刻的事件</legend>
            <div class="reminder-time-fields">
              <label
                >较早提醒（提前小时）<input
                  v-model.number="form.early_hours"
                  type="number"
                  min="0"
                  max="168"
                  step="1"
                  required
              /></label>
              <label
                >临近提醒（提前分钟）<input
                  v-model.number="form.late_minutes"
                  type="number"
                  min="0"
                  max="1440"
                  step="1"
                  required
              /></label>
            </div>
            <p class="caption">默认提前 24 小时、30 分钟。填 0 关闭对应提醒；相同时点合并。</p>
          </fieldset>
          <fieldset>
            <legend>只有日期的事件 · 北京时间 09:00</legend>
            <label class="check-row"
              ><input v-model="form.date_previous" type="checkbox" />前一天提醒</label
            >
            <label class="check-row"
              ><input v-model="form.date_same" type="checkbox" />当天提醒</label
            >
            <label class="check-row"
              ><input v-model="form.fund_end" type="checkbox" />同时提醒基金开放最后一天</label
            >
            <p class="caption">09:00 是提醒时间。基金实际受理时间、交易日和限制条件以公告为准。</p>
          </fieldset>
          <button class="primary" :disabled="busy" type="submit">
            {{ busy ? '保存中…' : '保存提醒设置' }}
          </button>
        </form>
        <aside class="evidence-panel reminder-agenda">
          <span class="eyebrow">NEXT REMINDERS</span>
          <h2>接下来会提醒什么</h2>
          <p class="caption">
            {{ data.settings.enabled ? '自动提醒已开启' : '自动提醒已暂停' }} · 时间均为北京时间
          </p>
          <p v-if="!data.upcoming.length" class="empty">
            暂无待发送提醒。开启后请关注未来事件并选择接收设备。
          </p>
          <ol v-else class="plain-list">
            <li v-for="item in data.upcoming" :key="item.id">
              <span class="step"
                >{{ formatTime(item.scheduled_at) }} · {{ reminderState(item) }}</span
              >
              <button class="reminder-event-link" @click="emit('event', item.event_id)">
                {{ item.title }}
              </button>
              <p class="caption">{{ item.body }}</p>
              <p class="caption">接收：{{ item.label }}</p>
              <p v-if="item.error" class="warning">{{ item.error }} · 将按有效期重试</p>
            </li>
          </ol>
          <p class="caption">
            最多列出最近 30 条计划。有效期最多 10
            分钟，有时刻的事件最迟到开始时刻；过期或新开启的关注不补发过去的提醒。
          </p>
          <details v-if="data.watches.length" class="source-details">
            <summary>单独设置的事件 · {{ data.watches.length }}</summary>
            <ul class="plain-list">
              <li v-for="item in data.watches" :key="item.event_id">
                <button @click="emit('event', item.event_id)">{{ item.title }}</button>
                <p class="caption">{{ item.enabled ? '单独关注' : '单独关闭' }}</p>
              </li>
            </ul>
          </details>
        </aside>
      </div>
      <section class="reminder-history" aria-label="自动提醒记录">
        <div class="agenda-heading">
          <h2>提醒记录</h2>
          <button :disabled="busy" @click="action(() => load())">刷新提醒记录</button>
        </div>
        <p class="caption">
          推送服务接受、设备回执与点击分别记录；设备回执不代表已看到锁屏通知。已被推送服务接受的通知无法撤回。
        </p>
        <p v-if="!data.history.length" class="empty">还没有自动提醒记录。</p>
        <ol v-else class="plain-list">
          <li v-for="item in data.history" :key="item.id">
            <span class="step">{{ formatTime(item.scheduled_at) }} · {{ item.label }}</span>
            <button class="reminder-event-link" @click="emit('event', item.event_id)">
              {{ item.title }}
            </button>
            <strong>{{ reminderState(item) }}</strong>
            <p class="caption">
              服务接受 {{ formatTime(item.accepted_at) }} · 设备回执
              {{ formatTime(item.received_at) }} · 点击 {{ formatTime(item.clicked_at) }}
            </p>
            <p v-if="item.error" class="caption">{{ item.error }}</p>
          </li>
        </ol>
      </section>
    </template>
  </section>
</template>
