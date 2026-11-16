<script setup lang="ts">
import { computed, nextTick, ref } from 'vue';

import { api, ApiError } from '../api';
import { eventTime, states, type CalendarEvent } from '../calendar';

type Time = Pick<CalendarEvent, 'start' | 'end' | 'precision'>;
const labels = { add: '新增', reschedule: '改期', cancel: '取消', update: '更新' } as const;
interface Preview {
  token: string;
  total: number;
  unchanged: number;
  stale: number;
  already_imported: boolean;
  changes: {
    action: keyof typeof labels;
    title: string;
    previous_title?: string;
    previous_status?: string;
    status: string;
    before?: Time;
    after: Time;
  }[];
}

const emit = defineEmits<{ imported: []; expired: [] }>();
const picker = ref<HTMLInputElement | null>(null);
const feedback = ref<HTMLElement | null>(null);
const file = ref<File | null>(null);
const preview = ref<Preview | null>(null);
const busy = ref(false);
const message = ref('');
const error = ref('');
const visibleRows = ref(12);
const summary = computed(() => {
  if (!preview.value) return '';
  const counts = { add: 0, reschedule: 0, cancel: 0, update: 0 };
  for (const change of preview.value.changes) counts[change.action]++;
  const parts = Object.entries(labels)
    .filter(([key]) => counts[key as keyof typeof counts])
    .map(([key, label]) => `${label} ${counts[key as keyof typeof counts]} 项`);
  if (preview.value.unchanged) parts.push(`未变化 ${preview.value.unchanged} 项`);
  if (preview.value.stale) parts.push(`跳过旧版本 ${preview.value.stale} 项`);
  return parts.join(' · ');
});

function reset(clearInput = true) {
  file.value = null;
  preview.value = null;
  error.value = '';
  message.value = '';
  visibleRows.value = 12;
  if (clearInput && picker.value) picker.value.value = '';
}

async function inspectFile(event: Event) {
  const selected = (event.target as HTMLInputElement).files?.[0];
  reset(false);
  if (!selected) return;
  if (selected.size > 2 * 1024 * 1024) {
    error.value = '文件超过 2 MiB，请重新选择。';
    return;
  }
  file.value = selected;
  await submit(false);
}

async function submit(confirm: boolean) {
  const token = preview.value?.token;
  if (!file.value || busy.value || (confirm && (!token || !preview.value?.changes.length))) return;
  busy.value = true;
  error.value = '';
  const suffix = confirm && token ? `?confirm=${encodeURIComponent(token)}` : '';
  try {
    const result = await api<Preview>(`calendar-imports/bls${suffix}`, 'POST', file.value);
    if (result.already_imported || confirm) {
      reset();
      message.value = result.already_imported
        ? '此文件已导入。'
        : `已导入 ${result.changes.length} 项日程变更。`;
      emit('imported');
    } else {
      preview.value = result;
    }
  } catch (cause) {
    if (cause instanceof ApiError && cause.status === 401) emit('expired');
    preview.value = null;
    error.value =
      cause instanceof ApiError && cause.status === 409
        ? '日程在预览后发生变化，请重新预览。'
        : cause instanceof Error
          ? cause.message
          : '文件读取失败，请重试。';
  } finally {
    busy.value = false;
    await nextTick();
    feedback.value?.focus();
  }
}

function discard() {
  reset();
  picker.value?.focus();
}
</script>

<template>
  <section class="ics-import" aria-labelledby="ics-heading">
    <h2 id="ics-heading">手动导入 BLS 日历</h2>
    <p id="ics-help" class="caption">
      选择从 BLS 获取的 .ics 文件（最多 2 MiB），核对变更后导入。文件未列出的日程会保留。
    </p>
    <label for="bls-ics-file">选择 ICS 文件</label>
    <input
      id="bls-ics-file"
      ref="picker"
      type="file"
      accept=".ics,text/calendar"
      :disabled="busy"
      aria-describedby="ics-help"
      @change="inspectFile"
    />
    <p v-if="busy" role="status">正在处理日历文件…</p>
    <p v-if="error" ref="feedback" tabindex="-1" role="alert" class="warning">{{ error }}</p>
    <p v-else-if="message" ref="feedback" tabindex="-1" role="status">{{ message }}</p>
    <template v-if="preview">
      <h3 ref="feedback" tabindex="-1">导入预览 · {{ file?.name }}</h3>
      <p role="status">{{ summary }}</p>
      <ul v-if="preview.changes.length" class="plain-list ics-changes">
        <li v-for="(item, index) in preview.changes.slice(0, visibleRows)" :key="index">
          <strong>{{ labels[item.action] }} · {{ item.title }}</strong>
          <p v-if="item.previous_title" class="caption">原标题：{{ item.previous_title }}</p>
          <p v-if="item.previous_status && item.previous_status !== item.status" class="caption">
            {{ states[item.previous_status] }} → {{ states[item.status] }}
          </p>
          <p v-if="item.action === 'reschedule' && item.before" class="caption">
            原定 {{ eventTime(item.before) }}
          </p>
          <p>{{ item.action === 'reschedule' ? '改为 ' : '' }}{{ eventTime(item.after) }}</p>
        </li>
      </ul>
      <button
        v-if="visibleRows < preview.changes.length"
        :disabled="busy"
        @click="visibleRows += 24"
      >
        显示更多变更
      </button>
      <div class="ics-actions">
        <button
          v-if="preview.changes.length"
          class="primary"
          :disabled="busy"
          @click="submit(true)"
        >
          确认导入 {{ preview.changes.length }} 项
        </button>
        <button :disabled="busy" @click="discard">
          {{ preview.changes.length ? '放弃本次导入' : '关闭预览' }}
        </button>
      </div>
    </template>
    <button v-else-if="error && file" :disabled="busy" @click="submit(false)">重新预览</button>
  </section>
</template>
