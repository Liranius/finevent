<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { api, ApiError } from '../api';
import {
  importanceName,
  firstSeenName,
  newsRowTimes,
  newsTime,
  relationName,
  safeNewsURL,
  sourceName,
  topicNames,
  type NewsDetail,
  type NewsMeta,
  type NewsPage,
  type NewsEntry,
} from '../news';
import { defaults, searchFields, type ReaderFilters } from '../news-search';
import { linkedOrigin, returnToList, type NewsNavigation } from '../workspace-navigation';
import CalendarLinks from './CalendarLinks.vue';
import NewsAnalysis from './NewsAnalysis.vue';
import ReaderSearch from './ReaderSearch.vue';

const props = defineProps<{ navigation?: NewsNavigation }>();
const emit = defineEmits<{ expired: []; remember: [state: NewsNavigation] }>();
const route = useRoute();
const router = useRouter();
const meta = ref<NewsMeta | null>(null);
const feed = ref<NewsPage | null>(null);
const detail = ref<NewsDetail | null>(null);
const selected = computed(() => (route.name === 'news-detail' ? Number(route.params.id) : 0));
const busy = ref(false);
const detailBusy = ref(false);
const error = ref('');
const pollError = ref('');
const changed = ref(false);
const search = ref('');
const filters = ref<ReaderFilters>({ ...defaults, range: '7' });
const fields = computed(() =>
  searchFields(
    [
      { value: 'unclassified', label: '未分类' },
      ...(meta.value?.categories ?? []).map((t) => ({ value: t.id, label: t.name })),
    ],
    (meta.value?.industries ?? []).map((t) => ({ value: t.id, label: t.name })),
    (meta.value?.sources ?? []).map((source) => ({ value: source, label: sourceName(source) })),
  ),
);
const lastSelected = ref(0);
let rowTop: number | undefined;
let pages = 0;
let anchorTime: number | undefined;
function queryForFilters() {
  const { range, ...values } = filters.value;
  return new URLSearchParams({
    ...values,
    q: search.value.trim(),
    since: range === '0' ? '0' : String(Math.floor(Date.now() / 1000) - Number(range) * 86400),
  });
}
let applied = queryForFilters();
function snapshot(): NewsNavigation {
  return {
    filters: { ...filters.value, q: search.value },
    applied: applied.toString(),
    lastSelected: lastSelected.value,
    pages,
    rowTop,
    anchorTime,
  };
}
function rememberList() {
  if (route.name !== 'news') return;
  router.options.history.replace(route.fullPath, { newsReader: { ...snapshot() } });
}
function restore(state: NewsNavigation) {
  const { q, ...values } = state.filters;
  filters.value = { ...defaults, ...values };
  search.value = q;
  applied = new URLSearchParams(state.applied);
  lastSelected.value = state.lastSelected ?? 0;
  rowTop = state.rowTop;
  anchorTime = state.anchorTime;
}
if (props.navigation) restore(props.navigation);
const byPublication = computed(() => filters.value.order === 'published');
const groups = computed(() => {
  const days = new Map<string, { item: NewsEntry; times: ReturnType<typeof newsRowTimes> }[]>();
  for (const item of feed.value?.items ?? []) {
    const times = newsRowTimes(item, byPublication.value);
    const row = { item, times };
    const group = days.get(times.day);
    if (group) group.push(row);
    else days.set(times.day, [row]);
  }
  return days;
});
function listTime(item: NewsPage['items'][number]) {
  return byPublication.value ? item.published_at : item.first_seen;
}
let feedRequest = 0,
  detailRequest = 0;
let disposed = false;

let timer: ReturnType<typeof setInterval> | undefined;
let checking = false;
let detailWatermark: number | undefined;
let retryMore = false;
let navigationRequest = 0;
function handleError(cause: unknown) {
  if (cause instanceof ApiError && cause.status === 401) emit('expired');
  return cause instanceof Error ? cause.message : '新闻暂时无法读取，请重试。';
}
async function load(more = false) {
  const request = ++feedRequest;
  retryMore = more;
  busy.value = true;
  error.value = '';
  const query = new URLSearchParams(applied);
  if (more && feed.value?.next) query.set('cursor', feed.value.next);
  try {
    const [next, info] = await Promise.all([
      api<NewsPage>(`news?${query}`),
      api<NewsMeta>('news/meta'),
    ]);
    if (disposed || request !== feedRequest) return;
    if (more && feed.value) {
      const existing = new Set(feed.value.items.map((item) => item.id));
      feed.value = {
        ...next,
        watermark: feed.value.watermark,
        items: [...feed.value.items, ...next.items.filter((item) => !existing.has(item.id))],
      };
      pages++;
    } else {
      feed.value = next;
      pages = 1;
      changed.value = false;
      pollError.value = '';
    }
    meta.value = info;
    return true;
  } catch (cause) {
    if (!disposed && request === feedRequest) {
      if (!more) feed.value = null;
      error.value = handleError(cause);
    }
  } finally {
    if (!disposed && request === feedRequest) busy.value = false;
  }
}
function clearFilters() {
  search.value = '';
  void apply({ ...defaults });
}
async function apply(next = filters.value) {
  ++navigationRequest;
  filters.value = next;
  lastSelected.value = 0;
  rowTop = undefined;
  anchorTime = undefined;
  feed.value = null;
  applied = queryForFilters();
  pages = 0;
  rememberList();
  await load();
}
async function loadDetail(id: number) {
  const request = ++detailRequest;
  detailBusy.value = true;
  detail.value = null;
  error.value = '';
  try {
    const next = await api<NewsDetail>(`news/${id}`);
    if (disposed || request !== detailRequest) return;
    detail.value = next;
    await nextTick();
    if (!disposed && request === detailRequest) {
      window.scrollTo(0, 0);
      document.getElementById('news-detail-heading')?.focus({ preventScroll: true });
    }
  } catch (cause) {
    if (!disposed && request === detailRequest) error.value = handleError(cause);
  } finally {
    if (!disposed && request === detailRequest) detailBusy.value = false;
  }
}
function open(item: NewsEntry) {
  const id = item.id;
  if (route.name === 'news') {
    lastSelected.value = id;
    anchorTime = listTime(item);
    rowTop = document.getElementById('news-item-' + id)?.getBoundingClientRect().top;
    rememberList();
  }
  void router.push({ name: 'news-detail', params: { id } });
}
// New records may move the anchor beyond its old page on a reload. Stop once the
// server's descending (timestamp, id) order has passed its former position.
function anchorIsBeyondPage() {
  const last = feed.value?.items.at(-1);
  if (!last || anchorTime === undefined || !lastSelected.value) return false;
  if (feed.value?.items.some((item) => item.id === lastSelected.value)) return false;
  const time = listTime(last);
  return time > anchorTime || (time === anchorTime && last.id > lastSelected.value);
}
async function syncRoute() {
  if (route.meta.workspace !== 'news') return;
  const request = ++navigationRequest;
  const path = route.fullPath;
  if (selected.value) {
    await loadDetail(selected.value);
    return;
  }
  ++detailRequest;
  detail.value = null;
  detailBusy.value = false;
  error.value = '';
  const saved = router.options.history.state.newsReader as NewsNavigation | undefined;
  const previousQuery = applied.toString();
  if (saved) restore(saved);
  if (!feed.value || previousQuery !== applied.toString()) {
    if (!(await load())) return;
  }
  const current = () => !disposed && request === navigationRequest && route.fullPath === path;
  const targetPages = saved?.pages ?? props.navigation?.pages ?? 1;
  while (current() && feed.value?.next && (pages < targetPages || anchorIsBeyondPage())) {
    if (!(await load(true))) return;
  }
  await nextTick();
  if (!current()) return;
  const anchor = document.getElementById('news-item-' + lastSelected.value);
  if (anchor && typeof rowTop === 'number') {
    window.scrollBy(0, anchor.getBoundingClientRect().top - rowTop);
    // The anchor may now be at a page boundary. Load enough rows below it to
    // reach the original offset without being clamped to the document bottom.
    while (current() && feed.value?.next && anchor.getBoundingClientRect().top > rowTop + 1) {
      if (!(await load(true))) return;
      await nextTick();
      if (!current()) return;
      window.scrollBy(0, anchor.getBoundingClientRect().top - rowTop);
    }
    anchor.focus({ preventScroll: true });
  } else {
    window.scrollTo(0, 0);
    document.getElementById('news-heading')?.focus({ preventScroll: true });
  }
}
async function retry() {
  if (selected.value) return loadDetail(selected.value);
  if (retryMore && !(await load(true))) return;
  await syncRoute();
}
function relatedOriginLabel() {
  if (linkedOrigin(router, 'news-notifications')) return '报道通知记录';
  if (linkedOrigin(router, 'calendar-event')) return '事件详情';
}
function back() {
  if (relatedOriginLabel()) router.back();
  else returnToList(router, 'news');
}
watch(() => route.fullPath, syncRoute);
async function refresh() {
  const id = selected.value;
  await load();
  if (!disposed && id && selected.value === id) await loadDetail(id);
}
async function checkChanges() {
  if (disposed || busy.value || checking || (!feed.value && !selected.value)) return;
  checking = true;
  const request = feedRequest;
  const after = feed.value?.watermark ?? detailWatermark;
  try {
    const [changes, info] = await Promise.allSettled([
      api<{ watermark: number; changed: number; reset: boolean }>(
        `news/changes?after=${after ?? 0}`,
      ),
      api<NewsMeta>('news/meta'),
    ]);
    if (disposed || request !== feedRequest) return;
    if (info.status === 'fulfilled') meta.value = info.value;
    if (changes.status === 'fulfilled') {
      // A direct link starts at the current watermark, excluding older changes.
      if (after === undefined) detailWatermark = changes.value.watermark;
      else changed.value = changed.value || changes.value.reset || changes.value.changed > 0;
    }
    const failure = [changes, info].find((result) => result.status === 'rejected');
    pollError.value = failure ? handleError(failure.reason) : '';
  } finally {
    checking = false;
  }
}
function onVisible() {
  if (document.visibilityState === 'visible') void checkChanges();
}
function labels(entry: NewsPage['items'][number]) {
  return entry.classification
    ? topicNames(entry.classification.categories, meta.value?.categories) || '未分类'
    : '分类处理中';
}
onMounted(async () => {
  if (selected.value) void checkChanges();
  await syncRoute();
  if (disposed) return;
  timer = setInterval(onVisible, 30000);
  document.addEventListener('visibilitychange', onVisible);
});
onUnmounted(() => {
  emit('remember', snapshot());
  disposed = true;
  ++feedRequest;
  ++detailRequest;
  clearInterval(timer);
  document.removeEventListener('visibilitychange', onVisible);
});
</script>

<template>
  <section class="news-workspace" aria-label="报道工作区">
    <div v-if="!selected" class="news-reader-heading">
      <h1 id="news-heading" tabindex="-1">报道记录</h1>
      <ReaderSearch
        v-model:search="search"
        :model-value="filters"
        :fields="fields"
        placeholder="搜索标题或正文"
        @update:model-value="apply"
        @submit="apply()"
      />
    </div>
    <p v-if="error" class="message error" role="alert">
      {{ error }}
      <button :disabled="busy || detailBusy" @click="retry">重试新闻</button>
    </p>
    <p v-if="pollError" class="message error" role="alert">
      自动检查更新失败：{{ pollError }} <button @click="checkChanges">重试更新检查</button>
    </p>
    <p v-if="changed" class="message" role="status">
      有新增报道或修订。<button :disabled="busy || detailBusy" @click="refresh">
        更新当前内容
      </button>
    </p>
    <p v-if="!selected && feed?.backlog" class="caption" role="status">
      {{ feed.backlog }} 条历史报道正在补齐分类，筛选结果可能暂不完整。
    </p>
    <template v-if="!selected">
      <div class="news-stream-meta">
        <span role="status">{{ feed ? '已加载 ' + feed.items.length + ' 篇报道' : '' }}</span>
        <span>按{{ byPublication ? '来源发布' : '首次记录' }} · 北京时间</span>
        <button class="quiet" :disabled="busy" @click="refresh">刷新新闻</button>
      </div>
      <p v-if="!feed && busy" role="status" class="empty">正在读取已采集报道…</p>
      <p v-else-if="feed && !feed.items.length" class="empty">
        没有符合筛选的报道。<button
          v-if="search || fields.some((field) => filters[field.key] !== defaults[field.key])"
          @click="clearFilters"
        >
          清除筛选
        </button>
      </p>
      <section v-for="[day, items] in groups" :key="day" class="news-day">
        <h2>{{ day }}</h2>
        <ol class="news-list" aria-label="报道列表">
          <li
            v-for="{ item, times } in items"
            :key="item.id"
            class="news-row"
            :class="{ 'news-just-read': item.id === lastSelected }"
          >
            <button
              :id="'news-item-' + item.id"
              class="news-title"
              :aria-label="item.title"
              :aria-describedby="`news-clock-${item.id} news-meta-${item.id}`"
              @click="open(item)"
            >
              <span :id="'news-clock-' + item.id" class="news-clock">{{ times.clock }}</span>
              <span class="news-record"
                ><strong>{{ item.title }}</strong>
                <span :id="'news-meta-' + item.id" class="news-row-meta">
                  <span>{{ sourceName(item.source) }}</span>
                  <span class="news-secondary-time">{{ times.secondary }}</span>
                  <span v-if="times.earlierPublication" class="news-earlier-publication">{{
                    times.earlierPublication
                  }}</span>
                  <span>{{ labels(item) }}</span>
                  <span v-if="item.classification?.industries.length" class="news-industries"
                    >提及：{{
                      topicNames(item.classification.industries.slice(0, 2), meta?.industries, '、')
                    }}</span
                  >
                  <span v-if="item.classification?.importance === 3" class="news-rule-important"
                    >重要 · 规则</span
                  >
                  <span v-if="item.version > 1">修订 {{ item.version }}</span>
                  <span v-if="item.id === lastSelected" class="news-read-marker">刚读到这里</span>
                </span></span
              >
              <span class="news-chevron" aria-hidden="true">›</span>
            </button>
          </li>
        </ol>
      </section>
      <button v-if="feed?.next" :disabled="busy" @click="load(true)">加载更多报道</button>
    </template>
    <template v-else>
      <div class="news-detail-actions">
        <button class="quiet news-back" @click="back">
          ← 返回{{ relatedOriginLabel() || '报道列表' }}
        </button>
        <button class="quiet" :disabled="busy || detailBusy" @click="refresh">刷新报道</button>
      </div>
      <p v-if="detailBusy" class="empty" role="status">正在读取报道证据…</p>
      <div v-if="detail" class="news-detail-grid">
        <article class="news-body">
          <span class="eyebrow">报道原文 · 第 {{ detail.item.version }} 版</span>
          <h1 id="news-detail-heading" tabindex="-1">{{ detail.item.title }}</h1>
          <p>
            {{ sourceName(detail.item.source) }} · 来源发布
            {{ newsTime(detail.item.published_at) }}
          </p>
          <a
            v-if="safeNewsURL(detail.item.url)"
            :href="safeNewsURL(detail.item.url)"
            target="_blank"
            rel="noopener noreferrer"
            >查看新闻原文 ↗</a
          >
          <p class="news-copy">
            {{ detail.item.content || '来源未提供正文，仅保留标题。' }}
          </p>
          <p class="caption">以上为来源文本；下方解读单独标明事实引用和模型推断。</p>
          <NewsAnalysis
            :key="detail.item.id"
            :news-id="detail.item.id"
            @expired="emit('expired')"
          />
          <CalendarLinks
            :key="`links-${detail.item.id}-${detail.item.version}`"
            :news="detail.item"
            @expired="emit('expired')"
            @reload="loadDetail(selected)"
          />
          <section class="news-related" aria-labelledby="news-related-heading">
            <h3 id="news-related-heading">跨来源证据</h3>
            <p class="caption">{{ detail.relation_scope }}</p>
            <p v-if="!detail.related.length">当前范围未找到关联报道。</p>
            <ul class="plain-list">
              <li v-for="relation in detail.related" :key="relation.item.id">
                <span class="step"
                  >{{ relationName[relation.kind] }} · {{ sourceName(relation.item.source) }}</span
                ><button class="news-related-link" @click="open(relation.item)">
                  {{ relation.item.title }}</button
                ><span class="caption">来源发布 {{ newsTime(relation.item.published_at) }}</span>
              </li>
            </ul>
          </section>
          <details class="news-revisions">
            <summary>
              历史修订 · {{ detail.revisions.length
              }}{{ detail.revision_more ? '（仅列最近 30 版）' : '' }}
            </summary>
            <p v-if="!detail.revisions.length">尚无历史修订。</p>
            <article v-for="revision in detail.revisions" :key="revision.version">
              <h4>第 {{ revision.version }} 版 · {{ revision.title }}</h4>
              <p class="caption">
                归档 {{ newsTime(revision.saved_at) }} · 当时的来源发布时间
                {{ newsTime(revision.published_at) }}
              </p>
              <p class="news-copy">{{ revision.content || '未提供正文' }}</p>
              <a
                v-if="safeNewsURL(revision.url)"
                :href="safeNewsURL(revision.url)"
                target="_blank"
                rel="noopener noreferrer"
                >当时的原文链接 ↗</a
              >
              <p v-else class="caption">旧版本未记录有效原文链接。</p>
            </article>
          </details>
        </article>
        <aside class="news-evidence" aria-label="分类与时间证据">
          <h3>规则分类</h3>
          <p>{{ labels(detail.item) }}</p>
          <p>
            {{
              detail.item.classification
                ? topicNames(detail.item.classification.industries, meta?.industries) ||
                  '未匹配提及行业'
                : '行业分类处理中'
            }}
          </p>
          <p v-if="detail.item.classification">
            {{ importanceName[detail.item.classification.importance] }} · 规则重要性
          </p>
          <p class="caption">主题与行业为文本匹配，不作为事实确认或投资方向判断。</p>
          <details v-if="detail.item.classification">
            <summary>查看分类依据</summary>
            <ul>
              <li v-for="reason in detail.item.classification.reasons" :key="reason">
                {{ reason }}
              </li>
            </ul>
            <p v-if="!detail.item.classification.reasons.length">尚未匹配规则。</p>
            <p class="caption">规则版本 {{ detail.item.classification.rule_version }}</p>
          </details>
          <h3>时间记录</h3>
          <dl class="news-times">
            <dt>来源发布时间</dt>
            <dd>{{ newsTime(detail.item.published_at) }}</dd>
            <dt>{{ firstSeenName(detail.item.source) }}</dt>
            <dd>{{ newsTime(detail.item.first_seen) }}</dd>
            <dt>入库时间</dt>
            <dd>
              {{ newsTime(detail.item.ingested_at)
              }}{{ !detail.item.ingested_at ? ' · 旧数据未记录' : '' }}
            </dd>
            <dt>内容最近更新</dt>
            <dd>{{ newsTime(detail.item.updated_at) }}</dd>
            <dt>最近采集到</dt>
            <dd>{{ newsTime(detail.item.last_seen) }}</dd>
            <template v-if="detail.item.source.startsWith('trendradar:')">
              <dt>上游最近记录</dt>
              <dd>{{ newsTime(detail.item.source_observed_at) }}</dd>
            </template>
          </dl>
          <p v-if="detail.item.baseline" class="caption">
            首次接入时的历史条目，不计入采集延迟样本。
          </p>
          <p v-if="detail.item.source.startsWith('trendradar:')" class="caption">
            TrendRadar 上游约 30 分钟采集；首次记录沿用其数据库时间，finevent
            实际入库时间另列。来源发布时间未知时，不以采集时刻替代。
          </p>
        </aside>
      </div>
    </template>
  </section>
</template>
