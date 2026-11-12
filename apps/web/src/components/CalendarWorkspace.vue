<script setup lang="ts">
import { outlinedChevronLeft, outlinedChevronRight } from '@quasar/extras/material-icons-outlined';
import { QBtn } from 'quasar';
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue';
import { RouterLink, useRoute, useRouter } from 'vue-router';

import { api, ApiError, formatTime } from '../api';
import {
  agendaTime,
  beijingDay,
  calendarEntries,
  dayLabel,
  monthAgenda,
  blankEvent,
  eventDays,
  eventOrigin,
  eventTime,
  monthDays,
  reviewedTime,
  states,
  type CalendarData,
  type CalendarEvent,
  type EventDetail,
  type FundDraft,
} from '../calendar';
import { linkedOrigin, returnToList, type CalendarNavigation } from '../workspace-navigation';
import CalendarLinks from './CalendarLinks.vue';
import EventEditor from './EventEditor.vue';
import EventReminder from './EventReminder.vue';
import ReminderSettings from './ReminderSettings.vue';

const props = defineProps<{ navigation?: CalendarNavigation }>();
const emit = defineEmits<{ expired: []; devices: []; remember: [state: CalendarNavigation] }>();
const route = useRoute();
const router = useRouter();
const today = ref(beijingDay(new Date()));
const month = ref(props.navigation?.month ?? today.value.slice(0, 7));
const selectedDay = ref(props.navigation?.day ?? today.value);
const reminderHistory = computed(() => route.name === 'reminder-history');
const mode = computed(() =>
  route.name === 'calendar-event'
    ? 'detail'
    : route.name === 'fund' || route.name === 'fund-draft'
      ? 'fund'
      : route.name === 'calendar-new'
        ? 'manual'
        : route.name === 'calendar-sources'
          ? 'sources'
          : route.name === 'reminders' || reminderHistory.value
            ? 'reminders'
            : 'calendar',
);
const layout = ref<'month' | 'agenda'>(
  props.navigation?.layout ??
    (window.matchMedia('(max-width: 680px)').matches ? 'agenda' : 'month'),
);
const data = ref<CalendarData | null>(null);
const snapshotCount = computed(() =>
  (data.value?.sources ?? []).reduce(
    (total, source) =>
      total + (source.snapshots ?? []).reduce((sum, snapshot) => sum + snapshot.active_entries, 0),
    0,
  ),
);
const detail = ref<EventDetail | null>(null);
const editing = ref(false);
const draft = ref<FundDraft | null>(null);
const drafts = ref<FundDraft[]>([]);
const announcement = ref('');
const announcementURL = ref('');
const error = ref('');
const notice = ref('');
const working = ref(false);
const routing = ref(false);
const busy = computed(() => working.value || routing.value);
const heading = ref<HTMLElement | null>(null);
const filter = ref(props.navigation?.filter ?? 'all');
const manual = ref(blankEvent());
const days = computed(() => monthDays(month.value));
const entries = computed(() =>
  calendarEntries(
    (data.value?.events ?? []).filter(
      (event) => filter.value === 'all' || event.kind === filter.value,
    ),
  ),
);
const atDay = (day: string) =>
  entries.value.filter(({ start, end }) => start <= day && end >= day).map(({ event }) => event);
const groups = computed(() => {
  if (layout.value === 'month')
    return [{ day: selectedDay.value, events: atDay(selectedDay.value) }];
  const result = monthAgenda(entries.value, month.value);
  if (today.value.startsWith(month.value) && !result.some((group) => group.day === today.value)) {
    result.push({ day: today.value, events: [] });
    result.sort((a, b) => a.day.localeCompare(b.day));
  }
  return result;
});
const agendaCount = computed(() =>
  groups.value.reduce((count, group) => count + group.events.length, 0),
);
let timer: ReturnType<typeof setInterval> | undefined;
let requestID = 0;
let disposed = false;
let ready = false;
let routeRequest = 0;
const lastEvent = ref(props.navigation?.lastEvent ?? '');
let rowTop = props.navigation?.rowTop;
let loadedMonth = '';
function calendarPosition(): CalendarNavigation {
  return {
    day: selectedDay.value,
    month: month.value,
    layout: layout.value,
    filter: filter.value,
    lastEvent: lastEvent.value,
    rowTop,
  };
}
function rememberCalendar() {
  router.options.history.replace(route.fullPath, { calendarReader: { ...calendarPosition() } });
}
watch(
  [month, selectedDay, layout, filter],
  () => {
    if (route.name === 'calendar' && !routing.value) {
      lastEvent.value = '';
      rowTop = undefined;
      rememberCalendar();
    }
  },
  { flush: 'sync' },
);
const routeError = ref(false);
async function action(work: () => Promise<void>) {
  working.value = true;
  error.value = '';
  notice.value = '';
  try {
    await work();
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '操作失败，请稍后重试';
    if (cause instanceof ApiError && cause.status === 401) emit('expired');
  } finally {
    working.value = false;
  }
}
async function refresh() {
  today.value = beijingDay(new Date());
  const id = ++requestID;
  const requestedMonth = month.value;
  if (loadedMonth !== requestedMonth) data.value = null;
  const next = await api<CalendarData>(`calendar?from=${days.value[0]}&to=${days.value[41]}`);
  if (id !== requestID || month.value !== requestedMonth) return;
  data.value = next;
  loadedMonth = requestedMonth;
  const eventID = detail.value?.event.id;
  if (mode.value === 'detail' && eventID && !editing.value && !busy.value) {
    const latest = await api<EventDetail>(`calendar/${eventID}`);
    if (
      id === requestID &&
      mode.value === 'detail' &&
      !editing.value &&
      detail.value?.event.id === eventID &&
      latest.event.version >= detail.value.event.version
    )
      detail.value = latest;
  }
}
async function moveMonth(delta: number) {
  const date = new Date(`${month.value}-01T00:00:00Z`);
  date.setUTCMonth(date.getUTCMonth() + delta);
  month.value = date.toISOString().slice(0, 7);
  selectedDay.value = `${month.value}-01`;
  await action(refresh);
}
async function currentMonth() {
  today.value = beijingDay(new Date());
  month.value = today.value.slice(0, 7);
  selectedDay.value = today.value;
  await action(refresh);
  await nextTick();
  const target = document.getElementById(
    layout.value === 'agenda' ? 'agenda-' + today.value : 'day-' + today.value,
  );
  target?.focus({ preventScroll: true });
  target?.scrollIntoView({ block: 'center' });
}
async function focusHeading() {
  await nextTick();
  heading.value?.focus();
}
function openEvent(id: string, day?: string) {
  if (route.name === 'calendar') {
    if (day) selectedDay.value = day;
    lastEvent.value = id;
    rowTop = document.getElementById('calendar-event-' + id)?.getBoundingClientRect().top;
    rememberCalendar();
  }
  void router.push({
    name: 'calendar-event',
    params: { id },
    state: reminderHistory.value ? { linkedFrom: route.fullPath } : undefined,
  });
}
function eventOriginLabel() {
  if (linkedOrigin(router, 'reminder-history')) return '提醒记录';
  if (linkedOrigin(router, 'news-detail')) return '报道详情';
}
function navigate(next: 'calendar' | 'fund' | 'manual' | 'reminders') {
  if (next === 'calendar' && mode.value === 'detail') {
    if (eventOriginLabel()) router.back();
    else returnToList(router, 'calendar');
  } else void router.push({ name: next === 'manual' ? 'calendar-new' : next });
}
async function syncRoute() {
  if (route.meta.workspace !== 'calendar') return;
  const request = ++routeRequest;
  const path = route.fullPath;
  const name = route.name;
  const id = String(route.params.id ?? '');
  routing.value = true;
  routeError.value = false;
  if (name !== 'calendar' || data.value) error.value = '';
  notice.value = '';
  editing.value = false;
  detail.value = null;
  draft.value = null;
  const current = () => !disposed && request === routeRequest && path === route.fullPath;
  try {
    if (name === 'calendar') {
      const saved = router.options.history.state.calendarReader as CalendarNavigation | undefined;
      if (saved) {
        month.value = saved.month;
        selectedDay.value = saved.day;
        layout.value = saved.layout;
        filter.value = saved.filter;
        lastEvent.value = saved.lastEvent || lastEvent.value;
        rowTop = saved.rowTop;
      }
      if (!data.value || loadedMonth !== month.value) await action(refresh);
    } else if (name === 'calendar-sources') {
      await action(refresh);
    } else if (name === 'calendar-event') {
      const next = await api<EventDetail>(`calendar/${encodeURIComponent(id)}`);
      if (!current()) return;
      detail.value = next;
      lastEvent.value = id;
    } else if (name === 'fund-draft') {
      const next = await api<FundDraft>(`fund-drafts/${encodeURIComponent(id)}`);
      if (!current()) return;
      draft.value = next;
    } else if (name === 'fund') {
      const next = await api<FundDraft[]>('fund-drafts');
      if (!current()) return;
      drafts.value = next;
    } else if (name === 'calendar-new') manual.value = blankEvent();
    await nextTick();
    if (!current()) return;
    if (name === 'calendar') {
      const anchor = document.getElementById('calendar-event-' + lastEvent.value);
      if (anchor && typeof rowTop === 'number') {
        window.scrollBy(0, anchor.getBoundingClientRect().top - rowTop);
        anchor.focus({ preventScroll: true });
      } else (anchor ?? heading.value)?.focus();
    } else heading.value?.focus();
  } catch (cause) {
    if (!current()) return;
    routeError.value = true;
    error.value = cause instanceof Error ? cause.message : '页面暂时无法读取，请重试。';
    if (cause instanceof ApiError && cause.status === 401) emit('expired');
    await focusHeading();
  } finally {
    if (current()) routing.value = false;
  }
}
watch(
  () => route.fullPath,
  () => {
    if (ready) void syncRoute();
  },
);
async function extract() {
  await action(async () => {
    const path = route.fullPath;
    const next = await api<FundDraft>('fund-drafts', 'POST', {
      text: announcement.value,
      url: announcementURL.value,
    });
    if (!disposed && path === route.fullPath)
      await router.push({ name: 'fund-draft', params: { id: next.id } });
  });
}
function loadDraft(id: string) {
  void router.push({ name: 'fund-draft', params: { id } });
}
async function confirm(event: CalendarEvent) {
  if (!draft.value) return;
  await action(async () => {
    const path = route.fullPath;
    const saved = await api<CalendarEvent>(`fund-drafts/${draft.value!.id}/confirm`, 'POST', {
      event,
      acknowledged: true,
    });
    if (disposed || path !== route.fullPath) return;
    draft.value = null;
    announcement.value = '';
    announcementURL.value = '';
    await afterSave(saved);
    notice.value = '已确认入历，公告原文和限制条件已保存。';
  });
}
async function save(event: CalendarEvent) {
  await action(async () => {
    const path = route.fullPath;
    const saved = await api<CalendarEvent>(
      event.id ? `calendar/${event.id}` : 'calendar',
      event.id ? 'PUT' : 'POST',
      event,
    );
    if (disposed || path !== route.fullPath) return;
    await afterSave(saved);
    notice.value = '事件已保存，变更可在修订记录中查看。';
  });
}
async function afterSave(event: CalendarEvent) {
  month.value = eventDays(event)[0].slice(0, 7);
  selectedDay.value = eventDays(event)[0];
  const target = { name: 'calendar-event', params: { id: event.id } };
  if (router.resolve(target).fullPath === route.fullPath) await syncRoute();
  else await router.replace(target);
  await refresh();
}
function resetDraft() {
  void router.replace({ name: 'fund' });
}
onMounted(async () => {
  ready = true;
  await syncRoute();
  if (disposed) return;
  timer = setInterval(() => {
    if (mode.value !== 'reminders' && !busy.value && document.visibilityState === 'visible')
      void refresh().catch((cause: unknown) => {
        if (cause instanceof ApiError && cause.status === 401) emit('expired');
        else error.value = '日历刷新失败，保留上次读取的数据。请稍后重试。';
      });
  }, 30000);
});
onUnmounted(() => {
  emit('remember', calendarPosition());
  disposed = true;
  ++routeRequest;
  clearInterval(timer);
  requestID++;
});
</script>

<template>
  <section class="calendar-workspace" aria-label="财经日历工作区">
    <div class="page-heading" :class="{ 'calendar-list-heading': mode === 'calendar' }">
      <div>
        <button v-if="mode === 'detail'" class="quiet calendar-back" @click="navigate('calendar')">
          <span aria-hidden="true">← </span>返回{{ eventOriginLabel() || '财经日历' }}
        </button>
        <h1 ref="heading" tabindex="-1">
          {{
            mode === 'fund'
              ? '基金窗口录入'
              : mode === 'manual'
                ? '补录官方事件'
                : mode === 'detail'
                  ? (detail?.event.title ?? '事件详情')
                  : mode === 'sources'
                    ? '日历官方来源'
                    : mode === 'reminders'
                      ? reminderHistory
                        ? '提醒记录'
                        : '提醒设置'
                      : '财经日历'
          }}
        </h1>
      </div>
      <div v-if="!route.meta.settings && mode !== 'detail'" class="record-actions">
        <button v-if="mode !== 'calendar'" @click="navigate('calendar')">日历</button
        ><button :aria-current="mode === 'fund' ? 'page' : undefined" @click="navigate('fund')">
          基金录入</button
        ><button class="quiet" @click="navigate('manual')">补录事件</button>
      </div>
    </div>
    <p v-if="error" class="message error" role="alert">
      {{ error }} <button v-if="routeError" :disabled="busy" @click="syncRoute">重试页面</button>
    </p>
    <p v-if="notice" class="message" role="status">{{ notice }}</p>
    <template v-if="mode === 'calendar'">
      <div class="calendar-toolbar">
        <div class="month-navigation">
          <QBtn
            flat
            round
            dense
            :icon="outlinedChevronLeft"
            aria-label="上个月"
            :disable="busy"
            @click="moveMonth(-1)"
          />
          <h2 aria-live="polite">{{ month.replace('-', ' 年 ') }} 月</h2>
          <QBtn
            flat
            round
            dense
            :icon="outlinedChevronRight"
            aria-label="下个月"
            :disable="busy"
            @click="moveMonth(1)"
          />
          <button class="quiet" :disabled="busy" @click="currentMonth">今天</button>
        </div>
        <div class="calendar-filters">
          <label for="calendar-filter" class="sr-only">事件类别</label
          ><select id="calendar-filter" v-model="filter">
            <option value="all">全部事件</option>
            <option value="macro">宏观经济</option>
            <option value="monetary">货币政策</option>
            <option value="fund">基金窗口</option>
            <option value="other">其他事件</option>
          </select>
          <div class="view-switch" aria-label="日历视图">
            <button :aria-pressed="layout === 'month'" @click="layout = 'month'">月历</button
            ><button :aria-pressed="layout === 'agenda'" @click="layout = 'agenda'">议程</button>
          </div>
        </div>
      </div>
      <p v-if="!data" class="empty" role="status">
        {{ busy ? '正在读取日历…' : '日历读取失败，请重试。'
        }}<button v-if="!busy" @click="action(refresh)">重试</button>
      </p>
      <div v-else class="calendar-layout" :class="{ 'agenda-only': layout === 'agenda' }">
        <div v-if="layout === 'month'" class="month-panel">
          <div class="weekdays" aria-hidden="true">
            <span v-for="day in ['一', '二', '三', '四', '五', '六', '日']" :key="day">{{
              day
            }}</span>
          </div>
          <div class="month-grid" role="group" aria-label="选择日期">
            <button
              v-for="day in days"
              :id="'day-' + day"
              :key="day"
              class="day-cell"
              :class="{ outside: !day.startsWith(month), today: day === today }"
              :aria-pressed="selectedDay === day"
              :aria-current="day === today ? 'date' : undefined"
              :aria-label="`${day}，${atDay(day).length} 个事件`"
              @click="selectedDay = day"
            >
              <span>{{ Number(day.slice(-2)) }}</span
              ><small v-if="atDay(day).length">{{ atDay(day).length }} 项</small>
            </button>
          </div>
          <p class="caption">时刻统一为北京时间；仅日期的海外事件保留来源当地日期。</p>
        </div>
        <section class="agenda-panel" aria-labelledby="agenda-title">
          <div class="agenda-heading">
            <h2 id="agenda-title">{{ layout === 'month' ? dayLabel(selectedDay) : '本月议程' }}</h2>
            <span>{{ agendaCount }} 个事件</span>
          </div>
          <p v-if="!agendaCount" class="empty">暂无已收录事件。可切换月份或事件类别。</p>
          <div v-for="group in groups" :key="group.day" class="agenda-group">
            <h3
              v-if="layout === 'agenda'"
              :id="'agenda-' + group.day"
              tabindex="-1"
              class="agenda-date"
            >
              <time :datetime="group.day">{{ dayLabel(group.day) }}</time>
              <span v-if="group.day === today" class="agenda-today">今天</span>
            </h3>
            <p v-if="layout === 'agenda' && !group.events.length && agendaCount" class="caption">
              今天没有新开始的日程。
            </p>
            <ol v-if="group.events.length" class="agenda-list">
              <li v-for="event in group.events" :key="event.id">
                <button
                  :id="'calendar-event-' + event.id"
                  class="agenda-event"
                  :class="{ 'calendar-just-read': lastEvent === event.id }"
                  @click="openEvent(event.id, group.day)"
                >
                  <span class="event-clock">{{ agendaTime(event) }}</span>
                  <strong :class="{ cancelled: event.status === 'cancelled' }">{{
                    event.title
                  }}</strong>
                  <span class="event-meta"
                    >{{ eventOrigin(event) }} · {{ states[event.status]
                    }}<span v-if="event.importance === 3"> · 重要</span
                    ><span v-if="event.fund_code"> · 基金 {{ event.fund_code }}</span
                    ><span v-if="lastEvent === event.id" class="news-read-marker"
                      >刚读到这里</span
                    ></span
                  >
                </button>
              </li>
            </ol>
          </div>
        </section>
      </div>
      <p v-if="snapshotCount" class="message warning" role="status">
        {{ snapshotCount }}
        项日程来自已核对的官方快照。后续改期尚未自动核对；<RouterLink
          :to="{ name: 'calendar-sources' }"
          >查看官方来源与核对时间</RouterLink
        >。
      </p>
    </template>
    <template v-else-if="mode === 'sources'">
      <p v-if="!data" class="empty">
        官方来源暂未加载。<button :disabled="busy" @click="action(refresh)">重试</button>
      </p>
      <details v-if="data" class="source-details">
        <summary>
          官方来源 ·
          {{ data.sources.filter((source) => source.error || !source.last_success).length }}
          项待核查
        </summary>
        <p class="caption">
          {{
            data.sync_enabled
              ? '每 6 小时同步一次；失败保留已有日程。日程仍可能调整，覆盖截止日不代表此前所有日期都有事件。'
              : '当前服务未启用自动同步。'
          }}
        </p>
        <ul class="plain-list">
          <li v-for="source in data.sources" :key="source.id">
            <div class="record-heading">
              <a :href="source.url" target="_blank" rel="noopener noreferrer">{{ source.name }}</a
              ><span>上次成功 {{ formatTime(source.last_success) }}</span>
            </div>
            <p v-if="source.error" class="warning">{{ source.error }}</p>
            <p v-else-if="!source.last_success" class="warning">尚未成功同步</p>
            <p v-else-if="Date.now() / 1000 - source.last_success > 43200" class="warning">
              超过 12 小时未同步成功，日程可能过期
            </p>
            <p v-if="source.coverage_end" class="caption">
              上次同步 {{ source.count }} 项 · 覆盖至 {{ source.coverage_end }}
            </p>
            <p v-if="source.note" class="caption">{{ source.note }}</p>
            <div
              v-for="snapshot in source.snapshots?.filter((item) => item.active_entries > 0)"
              :key="snapshot.id"
              class="snapshot-record"
            >
              <strong>官方快照 · {{ snapshot.active_entries }} 项尚未由主源更新</strong>
              <p class="caption">
                核对 {{ reviewedTime(snapshot.reviewed_at) }} · 北京时间 · 日程至
                {{ snapshot.coverage_end }}
              </p>
              <p class="caption">快照导入不代表主源同步成功，也不能保证已包含后续改期。</p>
              <a :href="`/finevent/api/v1/calendar-snapshots/${snapshot.id}`">下载核对时原文</a>
            </div>
          </li>
        </ul>
      </details>
    </template>
    <template v-else-if="mode === 'fund'">
      <p v-if="route.name === 'fund-draft' && !draft" class="empty" role="status">
        {{ routing ? '正在读取草稿…' : '草稿读取失败。' }}
      </p>
      <div v-else-if="draft" class="entry-layout">
        <section class="primary-panel">
          <span class="step">第二步 / 核对并确认</span>
          <h2>开放窗口草稿</h2>
          <ul class="draft-warnings">
            <li v-for="warning in draft.warnings" :key="warning">{{ warning }}</li>
          </ul>
          <p v-if="draft.confirmed_id" class="message">
            这份草稿已入历。<button @click="openEvent(draft.confirmed_id)">查看事件</button>
          </p>
          <EventEditor
            v-else
            :key="draft.id"
            :event="draft.event"
            :busy="busy"
            fund
            confirm
            @save="confirm"
            @cancel="resetDraft"
          />
        </section>
        <aside class="evidence-panel">
          <h2>对照公告原文</h2>
          <a :href="draft.url" target="_blank" rel="noopener noreferrer">打开原文链接 ↗</a>
          <p class="original-text">{{ draft.text }}</p>
        </aside>
      </div>
      <div v-else class="entry-layout">
        <section class="primary-panel">
          <span class="step">第一步 / 留存公告</span>
          <h2>粘贴基金公告</h2>
          <p class="caption">提取只生成草稿，确认前不会出现在日历中。</p>
          <form @submit.prevent="extract">
            <label for="announcement-url">公告原文链接</label
            ><input
              id="announcement-url"
              v-model="announcementURL"
              type="url"
              required
              maxlength="2048"
            /><label for="announcement-text">公告文字</label
            ><textarea
              id="announcement-text"
              v-model="announcement"
              rows="12"
              required
              maxlength="3900"
              placeholder="粘贴包含基金代码、申购/赎回开放时间和限制条件的公告正文。"
            /><button class="primary" :disabled="busy">
              {{ busy ? '正在提取…' : '提取草稿' }}
            </button>
          </form>
        </section>
        <aside class="evidence-panel">
          <h2>待确认草稿</h2>
          <p v-if="!drafts.length" class="empty">暂无待确认草稿。</p>
          <ul class="plain-list">
            <li v-for="item in drafts" :key="item.id">
              <button @click="loadDraft(item.id)">
                {{ item.event.fund_code || '代码待确认' }} · {{ item.event.start || '日期待确认' }}
              </button>
              <p class="caption">{{ item.text.slice(0, 80) }}…</p>
            </li>
          </ul>
        </aside>
      </div>
    </template>
    <section v-else-if="mode === 'manual'" class="primary-panel manual-panel">
      <p class="caption">填写有原文依据的事件；尚未确定时刻时选择“仅日期”。</p>
      <EventEditor :event="manual" :busy="busy" @save="save" @cancel="navigate('calendar')" />
    </section>
    <ReminderSettings
      v-else-if="mode === 'reminders'"
      :key="route.name ?? undefined"
      :records-only="reminderHistory"
      @expired="emit('expired')"
      @devices="emit('devices')"
      @event="openEvent"
    />
    <template v-else-if="detail">
      <div class="entry-layout">
        <section class="primary-panel">
          <span class="step"
            >{{ eventOrigin(detail.event) }} · {{ states[detail.event.status] }} · 第
            {{ detail.event.version }} 版</span
          >
          <p class="event-datetime">{{ eventTime(detail.event) }}</p>
          <p class="caption">
            原始时区 {{ detail.event.timezone
            }}<span v-if="detail.event.precision === 'minute'">
              · 原始时间 {{ detail.event.start }}</span
            >
          </p>
          <p v-if="detail.event.fund_code">基金代码 {{ detail.event.fund_code }}</p>
          <template v-if="!editing"
            ><p class="original-text">{{ detail.event.conditions || '未记录额外条件。' }}</p>
            <button v-if="['manual', 'fund'].includes(detail.event.source)" @click="editing = true">
              修改日期、条件或状态
            </button></template
          ><EventEditor
            v-else
            :key="detail.event.id + detail.event.version"
            :event="detail.event"
            :busy="busy"
            :fund="detail.event.kind === 'fund'"
            @save="save"
            @cancel="editing = false"
          />
          <EventReminder
            v-if="!editing"
            :key="detail.event.id + ':' + detail.event.version"
            :event-id="detail.event.id"
            :cancelled="detail.event.status === 'cancelled'"
            @settings="navigate('reminders')"
            @expired="emit('expired')"
          />
        </section>
        <aside class="evidence-panel">
          <h2>来源与依据</h2>
          <div v-if="detail.event.provenance?.mode === 'snapshot'" class="snapshot-record">
            <strong>官方原文快照</strong>
            <p class="caption">
              原文采集 {{ reviewedTime(detail.event.provenance.retrieved_at) }} · 北京时间
            </p>
            <p class="caption">
              核对 {{ reviewedTime(detail.event.provenance.reviewed_at) }} · 北京时间
            </p>
            <p class="warning">此后改期尚未自动核对，请以官方最新公告为准。</p>
            <a :href="`/finevent/api/v1/calendar-snapshots/${detail.event.provenance.snapshot_id}`"
              >下载核对时原文</a
            >
          </div>
          <div v-else-if="detail.event.provenance?.mode === 'supplement'" class="snapshot-record">
            <strong>纽约联储补充来源</strong>
            <p class="caption">日程转引自纽约联储，覆盖不完整，不表示 BLS 主源恢复。</p>
            <a
              :href="detail.event.provenance.authority_url"
              target="_blank"
              rel="noopener noreferrer"
              >查看 BLS 指标页面 ↗</a
            >
          </div>
          <a :href="detail.event.url" target="_blank" rel="noopener noreferrer">查看原文 ↗</a>
          <p class="original-text">{{ detail.event.evidence }}</p>
        </aside>
      </div>
      <CalendarLinks
        :key="detail.event.id"
        :event="detail.event"
        @expired="emit('expired')"
        @reload="syncRoute"
      />
      <details class="source-details">
        <summary>修订记录 · {{ detail.revisions.length }} 版</summary>
        <ol class="plain-list">
          <li v-for="revision in detail.revisions" :key="revision.event.version">
            <strong
              >第 {{ revision.event.version }} 版 · {{ states[revision.event.status] }}</strong
            >
            <p>{{ eventTime(revision.event) }}</p>
            <p class="caption">记录于 {{ formatTime(revision.saved_at) }}</p>
            <p class="caption">{{ eventOrigin(revision.event) }}</p>
            <p class="original-text">{{ revision.event.conditions }}</p>
          </li>
        </ol>
      </details>
    </template>
    <p v-else-if="mode === 'detail'" class="empty" role="status">
      {{ routing ? '正在读取事件…' : '事件读取失败。' }}
    </p>
    <footer v-if="!reminderHistory">
      日程以官方最新公告为准。自动提醒需在提醒设置中开启并选择设备；改期、取消以最新日程为准。
    </footer>
  </section>
</template>
