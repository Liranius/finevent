<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue';
import { RouterLink, useRoute, useRouter } from 'vue-router';

import { api, ApiError } from '../api';
import {
  importanceName,
  firstSeenName,
  newsTime,
  relationName,
  safeNewsURL,
  sourceName,
  topicNames,
  type NewsDetail,
  type NewsMeta,
  type NewsPage,
} from '../news';
import { returnToList, type NewsNavigation } from '../workspace-navigation';
import CalendarLinks from './CalendarLinks.vue';
import NewsAnalysis from './NewsAnalysis.vue';
import NewsPushSettings from './NewsPushSettings.vue';
import SourceHealth from './SourceHealth.vue';

const props = defineProps<{ navigation?: NewsNavigation }>();
const emit = defineEmits<{ expired: []; devices: []; remember: [state: NewsNavigation] }>();
const route = useRoute();
const router = useRouter();
const meta = ref<NewsMeta | null>(null);
const feed = ref<NewsPage | null>(null);
const detail = ref<NewsDetail | null>(null);
const selected = ref(props.navigation?.lastSelected ?? 0);
const settingsOpen = computed(() => route.name === 'news-push');
const busy = ref(false);
const detailBusy = ref(false);
const error = ref('');
const pollError = ref('');
const changed = ref(false);
const filters = reactive({
  q: '',
  category: '',
  industry: '',
  source: '',
  importance: '0',
  order: 'discovered',
  range: '7',
  ...props.navigation?.filters,
});
let applied = new URLSearchParams(props.navigation?.applied);
let feedRequest = 0,
  detailRequest = 0;
let disposed = false;
let ready = false;
let lastSelected = props.navigation?.lastSelected ?? 0;
let timer: ReturnType<typeof setInterval> | undefined;
let checking = false;
function handleError(cause: unknown) {
  if (cause instanceof ApiError && cause.status === 401) emit('expired');
  return cause instanceof Error ? cause.message : '新闻暂时无法读取，请重试。';
}
async function load(more = false) {
  const request = ++feedRequest;
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
    } else {
      feed.value = next;
      changed.value = false;
      pollError.value = '';
    }
    meta.value = info;
  } catch (cause) {
    if (!disposed && request === feedRequest) {
      if (!more) feed.value = null;
      error.value = handleError(cause);
    }
  } finally {
    if (!disposed && request === feedRequest) busy.value = false;
  }
}
async function apply() {
  applied = new URLSearchParams({
    q: filters.q.trim(),
    category: filters.category,
    industry: filters.industry,
    source: filters.source,
    importance: filters.importance,
    order: filters.order,
    since:
      filters.range === '0'
        ? '0'
        : String(Math.floor(Date.now() / 1000) - Number(filters.range) * 86400),
  });
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
    if (!disposed && request === detailRequest)
      document.getElementById('news-detail-heading')?.focus();
  } catch (cause) {
    if (!disposed && request === detailRequest) error.value = handleError(cause);
  } finally {
    if (!disposed && request === detailRequest) detailBusy.value = false;
  }
}
function open(id: number) {
  void router.push({ name: 'news-detail', params: { id } });
}
async function syncRoute() {
  if (route.meta.workspace !== 'news') return;
  const path = route.fullPath;
  if (settingsOpen.value) {
    ++detailRequest;
    detailBusy.value = false;
    await nextTick();
    if (!disposed && path === route.fullPath) document.getElementById('news-heading')?.focus();
    return;
  }
  const id = route.name === 'news-detail' ? Number(route.params.id) : 0;
  const previous = lastSelected;
  selected.value = id;
  if (id) {
    lastSelected = id;
    await loadDetail(id);
  } else {
    ++detailRequest;
    detail.value = null;
    detailBusy.value = false;
    if (previous) error.value = '';
    if (previous && !feed.value && !busy.value) await load();
    await nextTick();
    if (!disposed && path === route.fullPath)
      (
        document.getElementById(`news-item-${previous}`) ?? document.getElementById('news-heading')
      )?.focus();
  }
}
function back() {
  returnToList(router, 'news');
}
watch(
  () => route.fullPath,
  () => {
    if (ready) void syncRoute();
  },
);
async function refresh() {
  await load();
  if (selected.value) await loadDetail(selected.value);
}
async function checkChanges() {
  if (disposed || document.visibilityState !== 'visible' || busy.value || checking || !feed.value)
    return;
  checking = true;
  const request = feedRequest;
  try {
    const [next, info] = await Promise.all([
      api<{ watermark: number; changed: number; reset: boolean }>(
        `news/changes?after=${feed.value.watermark}`,
      ),
      api<NewsMeta>('news/meta'),
    ]);
    if (!disposed) {
      if (request === feedRequest) changed.value = changed.value || next.reset || next.changed > 0;
      meta.value = info;
      pollError.value = '';
    }
  } catch (cause) {
    if (!disposed) pollError.value = handleError(cause);
  } finally {
    checking = false;
  }
}
function onVisible() {
  void checkChanges();
}
function labels(entry: NewsPage['items'][number]) {
  return entry.classification
    ? topicNames(entry.classification.categories, meta.value?.categories) || '未分类'
    : '分类处理中';
}
onMounted(async () => {
  if (props.navigation) await load();
  else await apply();
  if (disposed) return;
  ready = true;
  await syncRoute();
  if (disposed) return;
  timer = setInterval(() => void checkChanges(), 30000);
  document.addEventListener('visibilitychange', onVisible);
});
onUnmounted(() => {
  emit('remember', { filters: { ...filters }, applied: applied.toString(), lastSelected });
  disposed = true;
  ++feedRequest;
  ++detailRequest;
  clearInterval(timer);
  document.removeEventListener('visibilitychange', onVisible);
});
</script>

<template>
  <section class="news-workspace" aria-label="新闻事件流工作区">
    <div class="page-heading">
      <div>
        <span class="eyebrow">THE NEWS RECORD</span>
        <h1 id="news-heading" tabindex="-1">
          {{ settingsOpen ? '新闻关注与推送' : '新闻事件流' }}
        </h1>
        <p class="intro">
          {{
            settingsOpen
              ? '选好关注范围，让重要变化及时抵达。'
              : '先看发生了什么，再核对来源与修订。'
          }}
        </p>
      </div>
      <button v-if="!settingsOpen" :disabled="busy || detailBusy" @click="refresh">刷新新闻</button>
    </div>
    <nav class="section-nav" aria-label="新闻功能">
      <RouterLink :to="{ name: 'news' }" :aria-current="!settingsOpen ? 'page' : undefined"
        >报道记录</RouterLink
      >
      <RouterLink :to="{ name: 'news-push' }">关注与推送</RouterLink>
    </nav>
    <NewsPushSettings v-if="settingsOpen" @expired="emit('expired')" @devices="emit('devices')" />
    <template v-else>
      <p v-if="error" class="message error" role="alert">
        {{ error }}
        <button :disabled="busy || detailBusy" @click="selected ? loadDetail(selected) : load()">
          重试新闻
        </button>
      </p>
      <p v-if="pollError" class="message error" role="alert">
        自动检查更新失败：{{ pollError }} <button @click="checkChanges">重试更新检查</button>
      </p>
      <p v-if="changed" class="message" role="status">
        有新增报道或修订。<button :disabled="busy || detailBusy" @click="refresh">
          更新当前内容
        </button>
      </p>
      <p v-if="feed?.backlog" class="caption" role="status">
        {{ feed.backlog }} 条历史报道正在补齐分类，筛选结果可能暂不完整。
      </p>
      <template v-if="!selected">
        <form class="news-filters" aria-label="新闻筛选" @submit.prevent="apply">
          <label class="news-search"
            >标题或正文<input
              v-model="filters.q"
              type="search"
              maxlength="100"
              placeholder="搜索已采集内容"
          /></label>
          <label
            >类别<select v-model="filters.category">
              <option value="">全部类别</option>
              <option value="unclassified">未分类</option>
              <option v-for="tag in meta?.categories" :key="tag.id" :value="tag.id">
                {{ tag.name }}
              </option>
            </select></label
          >
          <label
            >提及行业<select v-model="filters.industry">
              <option value="">全部行业</option>
              <option v-for="tag in meta?.industries" :key="tag.id" :value="tag.id">
                {{ tag.name }}
              </option>
            </select></label
          >
          <label
            >规则重要性<select v-model="filters.importance">
              <option value="0">全部</option>
              <option value="2">关注及以上</option>
              <option value="3">重要</option>
            </select></label
          >
          <label
            >来源<select v-model="filters.source">
              <option value="">全部来源</option>
              <option v-for="source in meta?.sources" :key="source" :value="source">
                {{ sourceName(source) }}
              </option>
            </select></label
          >
          <label
            >首次记录范围<select v-model="filters.range">
              <option value="1">近 24 小时</option>
              <option value="7">近 7 天</option>
              <option value="30">近 30 天</option>
              <option value="0">全部留存</option>
            </select></label
          >
          <label
            >排序<select v-model="filters.order">
              <option value="discovered">首次记录 · 最新优先</option>
              <option value="published">来源发布 · 未知置后</option>
            </select></label
          >
          <button class="primary" :disabled="busy">{{ busy ? '读取中…' : '应用筛选' }}</button>
        </form>
        <p class="caption">
          时间均为北京时间。规则标签来自关键词；提及行业不等于受到影响，重要性不代表利好或利空。
        </p>
        <p v-if="!feed && busy" role="status" class="empty">正在读取已采集报道…</p>
        <p v-else-if="feed && !feed.items.length" class="empty">
          没有符合筛选的报道。可调整条件；没有采集数据时这里保持为空。
        </p>
        <ol v-if="feed?.items.length" class="news-list" aria-label="报道列表">
          <li v-for="item in feed.items" :key="item.id" class="news-row">
            <div class="news-timing">
              <span>来源发布</span><time>{{ newsTime(item.published_at) }}</time
              ><span>{{ firstSeenName(item.source) }} {{ newsTime(item.first_seen) }}</span>
            </div>
            <div class="news-record">
              <div class="news-row-meta">
                <span>{{ sourceName(item.source) }}</span
                ><span
                  v-if="item.classification"
                  :class="{ 'news-important': item.classification.importance === 3 }"
                  >{{ importanceName[item.classification.importance] }} · 规则</span
                ><span v-if="item.version > 1">修订 {{ item.version }}</span>
              </div>
              <button :id="`news-item-${item.id}`" class="news-title" @click="open(item.id)">
                {{ item.title }}
              </button>
              <p class="caption">
                {{ labels(item)
                }}<span v-if="item.classification?.industries.length">
                  · {{ topicNames(item.classification.industries, meta?.industries) }}</span
                >
              </p>
              <p v-if="item.source.startsWith('trendradar:')" class="news-lag">
                经 TrendRadar 导入 · 上游约 30 分钟采集，导入不等于即时发布
              </p>
            </div>
          </li>
        </ol>
        <button v-if="feed?.next" :disabled="busy" @click="load(true)">加载更多报道</button>
      </template>
      <template v-else>
        <button class="quiet news-back" @click="back">← 返回新闻列表</button>
        <p v-if="detailBusy" class="empty" role="status">正在读取报道证据…</p>
        <div v-if="detail" class="news-detail-grid">
          <article class="news-body">
            <span class="eyebrow">SOURCE RECORD · 第 {{ detail.item.version }} 版</span>
            <h2 id="news-detail-heading" tabindex="-1">{{ detail.item.title }}</h2>
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
                    >{{ relationName[relation.kind] }} ·
                    {{ sourceName(relation.item.source) }}</span
                  ><button class="news-related-link" @click="open(relation.item.id)">
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
      <details v-if="meta" class="news-channels">
        <summary>采集通道状态</summary>
        <p class="caption">
          华尔街见闻约每 2 分钟采集；TrendRadar 约每分钟检查数据库，上游约每 30
          分钟采集。成功导入不表示每个上游新闻源都正常。
        </p>
        <p v-if="!meta.collection_enabled">此服务未启用自动采集。</p>
        <p v-else-if="!meta.trendradar_enabled">TrendRadar 导入未启用。</p>
        <ul class="plain-list">
          <li v-for="channel in meta.channels" :key="channel.id">
            <strong>{{
              channel.id === 'trendradar' ? 'TrendRadar 数据库导入' : sourceName(channel.id)
            }}</strong>
            <p>
              最后成功 {{ newsTime(channel.last_success) }} · 最近尝试
              {{ newsTime(channel.last_attempt) }}
            </p>
            <p v-if="channel.error" class="news-warning">{{ channel.error }}</p>
            <p
              v-else-if="channel.last_attempt && meta.server_time - channel.last_attempt > 600"
              class="news-warning"
            >
              超过 10 分钟未取得新的采集记录，时效需核查。
            </p>
            <p v-else-if="!channel.last_attempt">尚无采集记录。</p>
            <p v-if="channel.gap" class="news-warning">
              历史缺口 {{ newsTime(channel.gap_at) }}：{{
                channel.gap
              }}。后续成功不代表该缺口已经补齐。
            </p>
          </li>
        </ul>
        <SourceHealth @expired="emit('expired')" />
      </details>
    </template>
  </section>
</template>
