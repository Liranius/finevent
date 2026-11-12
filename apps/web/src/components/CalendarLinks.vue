<script setup lang="ts">
import { nextTick, onActivated, onDeactivated, onMounted, onUnmounted, ref } from 'vue';
import { RouterLink } from 'vue-router';

import { api, ApiError } from '../api';
import { beijingDay, eventOrigin, eventTime, states, type CalendarEvent } from '../calendar';
import type { CalendarLink, CalendarLinkPage } from '../calendar-links';
import { newsTime, safeNewsURL, sourceName, type NewsEntry } from '../news';

const props = defineProps<{ news?: NewsEntry; event?: CalendarEvent }>();
const emit = defineEmits<{ expired: []; reload: [] }>();
const links = ref<CalendarLinkPage | null>(null);
const error = ref('');
const notice = ref('');
const loading = ref(false);
const saving = ref(false);
const searching = ref(false);
const searchOpen = ref(false);
const query = ref('');
const day = props.news?.published_at ? new Date(props.news.published_at * 1000) : new Date();
const from = ref(beijingDay(new Date(day.getTime() - 7 * 86400000)));
const to = ref(beijingDay(new Date(day.getTime() + 7 * 86400000)));
const results = ref<{ items: CalendarEvent[]; more: boolean } | null>(null);
const chosen = ref<CalendarEvent | null>(null);
const revision = ref(0);
const note = ref('');
const removeID = ref(0);
const heading = ref<HTMLElement | null>(null);
const noteInput = ref<HTMLTextAreaElement | null>(null);
let disposed = false;
let active = true;
let request = 0;
let paged = false;
let timer: ReturnType<typeof setInterval> | undefined;
function problem(cause: unknown) {
  if (cause instanceof ApiError && cause.status === 401) emit('expired');
  return cause instanceof Error ? cause.message : '关联暂时无法读取，请重试。';
}
async function load(more = false, background = false) {
  if (!active || saving.value || loading.value) return;
  const token = ++request;
  loading.value = true;
  if (!background) error.value = '';
  const endpoint = props.news
    ? `news/${props.news.id}/calendar-links`
    : `calendar/${props.event!.id}/news-links`;
  try {
    const page = await api<CalendarLinkPage>(
      `${endpoint}?before=${more ? links.value?.next || 0 : 0}`,
    );
    if (disposed || !active || token !== request) return;
    links.value =
      more && links.value
        ? {
            ...page,
            items: [
              ...links.value.items,
              ...page.items.filter((item) => !links.value!.items.some((old) => old.id === item.id)),
            ],
          }
        : page;
    if (!background) paged = more;
  } catch (cause) {
    if (!disposed && active && token === request) error.value = problem(cause);
  } finally {
    if (token === request) loading.value = false;
  }
}
async function search() {
  searching.value = true;
  error.value = '';
  try {
    results.value = await api(
      `calendar-link-search?${new URLSearchParams({ from: from.value, to: to.value, q: query.value.trim() })}`,
    );
  } catch (cause) {
    error.value = problem(cause);
  } finally {
    searching.value = false;
  }
}
async function choose(event: CalendarEvent, link?: CalendarLink) {
  chosen.value = event;
  revision.value = link?.id ?? 0;
  note.value = link?.note ?? '';
  notice.value = '';
  await nextTick();
  noteInput.value?.focus();
}
async function cancel() {
  chosen.value = null;
  await nextTick();
  heading.value?.focus();
}
async function save(link?: CalendarLink) {
  const event = link?.event ?? chosen.value;
  if (!props.news || !event) return;
  saving.value = true;
  error.value = '';
  notice.value = '';
  try {
    await api(`news/${props.news.id}/calendar-links/${event.id}`, link ? 'DELETE' : 'PUT', {
      revision: link?.id ?? revision.value,
      news_version: props.news.version,
      event_version: event.version,
      note: note.value,
    });
    chosen.value = null;
    removeID.value = 0;
    notice.value = link ? '关联已解除。' : '关联已确认，两端详情均可查看。';
    saving.value = false;
    await load();
    await nextTick();
    heading.value?.focus();
  } catch (cause) {
    error.value = problem(cause);
  } finally {
    saving.value = false;
  }
}
function start() {
  clearInterval(timer);
  timer = setInterval(() => {
    if (document.visibilityState === 'visible' && !chosen.value && !removeID.value && !paged)
      void load(false, true);
  }, 30000);
}
onMounted(() => {
  void load();
  start();
});
onActivated(() => {
  active = true;
  start();
  void load();
});
onDeactivated(() => {
  active = false;
  ++request;
  loading.value = false;
  clearInterval(timer);
});
onUnmounted(() => {
  disposed = true;
  ++request;
  clearInterval(timer);
});
</script>

<template>
  <section class="calendar-links" :aria-label="news ? '关联日程' : '相关报道'">
    <div class="record-actions">
      <h3 ref="heading" tabindex="-1">{{ news ? '关联日程' : '相关报道' }}</h3>
      <button :disabled="loading || saving" @click="load()">刷新关联</button>
    </div>
    <p class="caption">由你核对并确认的关联；不代表来源已独立核实，也不改变日程和提醒。</p>
    <p v-if="error" class="message error" role="alert">
      {{ error }} <button :disabled="saving" @click="emit('reload')">重新载入详情</button>
    </p>
    <p v-if="notice" class="message" role="status">{{ notice }}</p>
    <p v-if="!links" role="status">{{ loading ? '正在读取关联…' : '关联尚未读取。' }}</p>
    <p v-else-if="!links.items.length">
      尚无已确认关联。{{ news ? '可查找日程并填写依据。' : '请从新闻详情查找并关联此日程。' }}
    </p>
    <ul v-if="links" class="plain-list">
      <li v-for="link in links.items" :key="link.id">
        <RouterLink
          class="news-related-link"
          :to="
            news
              ? { name: 'calendar-event', params: { id: link.event.id } }
              : { name: 'news-detail', params: { id: link.news.id } }
          "
          >{{ news ? link.event.title : link.news.title }}</RouterLink
        >
        <p class="caption">
          {{
            news
              ? `${eventOrigin(link.event)} · ${states[link.event.status]} · ${eventTime(link.event)}`
              : `${sourceName(link.news.source)} · 来源发布 ${newsTime(link.news.published_at)}`
          }}
        </p>
        <p>{{ link.note }}</p>
        <p v-if="link.changed" class="news-warning">报道或日程已有修订，关联待重新核对。</p>
        <details>
          <summary>确认依据 · {{ newsTime(link.confirmed_at) }}</summary>
          <p>报道第 {{ link.news_snapshot.version }} 版 · {{ link.news_snapshot.title }}</p>
          <p class="original-text">{{ link.news_snapshot.content || '来源未提供正文' }}</p>
          <p>
            日程第 {{ link.event_snapshot.version }} 版 · {{ link.event_snapshot.title }} ·
            {{ states[link.event_snapshot.status] }}
          </p>
          <p>{{ eventTime(link.event_snapshot) }}</p>
          <p class="original-text">{{ link.event_snapshot.evidence }}</p>
          <a
            v-if="safeNewsURL(link.event_snapshot.url)"
            :href="safeNewsURL(link.event_snapshot.url)"
            target="_blank"
            rel="noopener noreferrer"
            >确认时的日程原文 ↗</a
          >
        </details>
        <div v-if="news" class="record-actions">
          <button :disabled="saving" @click="choose(link.event, link)">重新核对关联</button>
          <button
            :aria-expanded="removeID === link.id"
            :disabled="saving"
            @click="removeID = removeID === link.id ? 0 : link.id"
          >
            解除关联
          </button>
          <template v-if="removeID === link.id">
            <span>确认解除这项关联？</span>
            <button :disabled="saving" @click="save(link)">确认解除</button>
            <button :disabled="saving" @click="removeID = 0">保留关联</button>
          </template>
        </div>
      </li>
    </ul>
    <button v-if="links?.next" :disabled="loading" @click="load(true)">加载更早关联</button>
    <template v-if="news">
      <button :aria-expanded="searchOpen" @click="searchOpen = !searchOpen">查找日程</button>
      <form v-if="searchOpen" class="link-search" aria-label="查找日程" @submit.prevent="search">
        <p class="caption">
          日期范围默认取{{
            news.published_at ? '来源发布时间' : '今天（来源发布时间未知）'
          }}前后七天，仅用于查找，不自动建立关联。
        </p>
        <div class="link-search-fields">
          <label>日程开始范围<input v-model="from" type="date" required /></label>
          <label>日程结束范围<input v-model="to" type="date" required /></label>
          <label>日程标题或基金代码<input v-model="query" type="search" maxlength="100" /></label>
          <button :disabled="searching">{{ searching ? '正在查找…' : '查询日程' }}</button>
        </div>
        <p v-if="results && !results.items.length">此范围没有匹配日程，请调整日期或关键词。</p>
        <p v-if="results?.more" class="news-warning">
          仅显示前 30 项，请缩小日期范围或补充关键词。
        </p>
        <ul class="plain-list">
          <li v-for="candidate in results?.items" :key="candidate.id">
            <strong>{{ candidate.title }}</strong>
            <p class="caption">
              {{ eventOrigin(candidate) }} · {{ states[candidate.status] }} ·
              {{ eventTime(candidate) }}
            </p>
            <button
              type="button"
              :disabled="saving || links?.items.some((item) => item.event.id === candidate.id)"
              @click="choose(candidate)"
            >
              {{
                links?.items.some((item) => item.event.id === candidate.id)
                  ? '已有关联'
                  : '核对此日程'
              }}
            </button>
          </li>
        </ul>
      </form>
      <form v-if="chosen" class="link-confirm" aria-label="核对关联" @submit.prevent="save()">
        <h4>{{ chosen.title }}</h4>
        <p>{{ eventOrigin(chosen) }} · {{ states[chosen.status] }} · {{ eventTime(chosen) }}</p>
        <p class="original-text">{{ chosen.evidence }}</p>
        <p v-if="chosen.conditions" class="original-text">限制条件：{{ chosen.conditions }}</p>
        <a
          v-if="safeNewsURL(chosen.url)"
          :href="safeNewsURL(chosen.url)"
          target="_blank"
          rel="noopener noreferrer"
          >核对日程原文 ↗</a
        >
        <p class="caption">
          将关联到上方报道第
          {{ news.version }} 版。请核对国家、指标、发布月份或基金代码；关联不会填入实际值或预期值。
        </p>
        <label
          >关联依据<textarea
            ref="noteInput"
            v-model="note"
            minlength="5"
            maxlength="1000"
            required
            rows="3"
            placeholder="说明报道与这次日程的对应依据"
          />
        </label>
        <div class="record-actions">
          <button class="primary" :disabled="saving || note.trim().length < 5">确认关联</button>
          <button type="button" :disabled="saving" @click="cancel">取消核对</button>
        </div>
      </form>
    </template>
  </section>
</template>
