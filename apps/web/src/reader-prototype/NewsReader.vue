<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue';
import { RouterLink, useRoute, useRouter } from 'vue-router';

import ReaderNav from '../components/ReaderNav.vue';
import ReaderSearch from '../components/ReaderSearch.vue';
import { returnToList } from '../workspace-navigation';
import {
  defaults,
  fields,
  sampleCutoff,
  filterStories,
  restoreFilters,
  storyTimestamp,
} from './filters';
import { stories, type Story } from './stories';

const route = useRoute();
const router = useRouter();
const filters = ref({ ...defaults });
const search = ref('');
const lastRead = ref('');
const heading = ref<HTMLElement>();
const article = computed(() => stories.find((item) => item.id === route.params.id));
const isList = computed(() => route.name === 'news');
const matched = computed(() => filterStories(filters.value, search.value));
const groups = computed(() => {
  const days = new Map<string, Story[]>();
  for (const item of matched.value) {
    const day = storyTimestamp(item, filters.value.order).slice(0, 10);
    if (!days.has(day)) days.set(day, []);
    days.get(day)!.push(item);
  }
  return days;
});
const section = computed(() => (route.name === 'story' ? 'news' : route.name));
const dateFormatter = new Intl.DateTimeFormat('zh-CN', {
  month: 'long',
  day: 'numeric',
  timeZone: 'UTC',
});
const weekdayFormatter = new Intl.DateTimeFormat('zh-CN', { weekday: 'long', timeZone: 'UTC' });

function rememberPosition(id: string) {
  const link = document.getElementById('story-' + id);
  // Attach the reading position to this list entry, so Back and Forward share it.
  router.options.history.replace(route.fullPath, {
    ...filters.value,
    search: search.value,
    lastRead: id,
    rowTop: link?.getBoundingClientRect().top ?? 0,
  });
}

function onNavClick(event: Event, dest: string) {
  if (
    dest === 'news' &&
    route.name === 'story' &&
    event instanceof MouseEvent &&
    !event.ctrlKey &&
    !event.metaKey &&
    !event.shiftKey &&
    !event.altKey
  ) {
    event.preventDefault();
    returnToList(router, 'news');
  }
}

async function enterPage() {
  const entry = router.options.history.state;
  if (isList.value) {
    filters.value = restoreFilters(entry);
    search.value = typeof entry.search === 'string' ? entry.search : '';
    lastRead.value = typeof entry.lastRead === 'string' ? entry.lastRead : '';
  }
  await nextTick();
  document.title =
    (article.value?.title ??
      ({ calendar: '财经日历', settings: '设置' } as Record<string, string>)[
        String(section.value)
      ] ??
      '报道') + ' · finevent';
  const anchor =
    isList.value && lastRead.value ? document.getElementById('story-' + lastRead.value) : null;
  if (anchor && typeof entry.rowTop === 'number') {
    window.scrollBy(0, anchor.getBoundingClientRect().top - entry.rowTop);
    anchor.focus({ preventScroll: true });
  } else {
    window.scrollTo(0, 0);
    heading.value?.focus({ preventScroll: true });
  }
}

function clearFilters() {
  filters.value = { ...defaults };
  search.value = '';
}

watch(() => route.fullPath, enterPage);
onMounted(enterPage);
</script>

<template>
  <div class="reader">
    <header class="reader-mast">
      <div class="reader-mast-inner">
        <span class="reader-brand"
          ><img :src="'/finevent/icon.svg'" width="28" height="28" alt="" />finevent</span
        >
        <ReaderNav :active="section" @navigate="onNavClick" />
      </div>
    </header>

    <main v-if="isList" class="reader-wrap">
      <div class="reader-intro">
        <div>
          <p class="reader-edition"><span>2024 / 10</span> 历史报道样本</p>
          <h1 ref="heading" tabindex="-1">报道记录</h1>
        </div>
        <ReaderSearch
          v-model="filters"
          v-model:search="search"
          :fields="fields"
          placeholder="搜索标题、正文或来源"
          >时间范围以
          {{ sampleCutoff.replace('T', ' ') }} 为准，首次记录时间为演示数据。</ReaderSearch
        >
      </div>

      <div class="reader-layout">
        <section aria-label="报道列表" class="reader-stream">
          <div class="reader-stream-meta">
            <span role="status">{{ matched.length }} 篇报道</span
            ><span
              >{{ filters.order === 'published' ? '按发布时间' : '按首次记录' }} · 北京时间</span
            >
          </div>
          <div v-if="!matched.length" class="reader-empty">
            <h2>没有匹配的报道</h2>
            <button @click="clearFilters">清除筛选</button>
          </div>
          <section v-for="[day, items] in groups" :key="day" class="reader-day">
            <h2>
              <template v-if="day"
                ><time :datetime="day">{{ dateFormatter.format(new Date(day)) }}</time
                ><span>{{ weekdayFormatter.format(new Date(day)) }}</span></template
              >
              <template v-else>发布时刻未知</template>
            </h2>
            <ol>
              <li
                v-for="item in items"
                :key="item.id"
                :class="{ 'just-read': item.id === lastRead }"
              >
                <RouterLink
                  :id="'story-' + item.id"
                  class="reader-item"
                  :to="{ name: 'story', params: { id: item.id } }"
                  :aria-label="item.title"
                  @click.capture="rememberPosition(item.id)"
                >
                  <span class="reader-clock">{{
                    storyTimestamp(item, filters.order).slice(11) || '仅日期'
                  }}</span>
                  <div class="reader-item-text">
                    <h3>{{ item.title }}</h3>
                    <div class="reader-item-source">
                      <span>{{ item.source }}</span
                      ><span>{{ item.category }}</span>
                      <em v-if="item.id === lastRead">刚读到这里</em>
                    </div>
                  </div>
                  <span class="reader-chevron" aria-hidden="true">›</span>
                </RouterLink>
              </li>
            </ol>
          </section>
        </section>
      </div>
    </main>

    <main v-else-if="route.name === 'story'" class="reader-wrap reader-detail">
      <button class="reader-back" @click="returnToList(router, 'news')">
        <span aria-hidden="true">←</span> 返回报道列表
      </button>
      <article v-if="article" :key="article.id" class="reader-article">
        <header>
          <p class="reader-edition">
            <span>{{ article.category }}</span> 历史报道样本
          </p>
          <h1 ref="heading" tabindex="-1">{{ article.title }}</h1>
          <p class="reader-byline">
            {{ article.source }}<span>·</span
            ><time :datetime="article.day">{{ article.day }} {{ article.time || '时刻未知' }}</time
            ><span v-if="article.time">北京时间</span>
          </p>
        </header>
        <div class="reader-body">
          <div class="reader-story-copy">
            <p class="reader-caption">原文摘编</p>
            <p v-for="paragraph in article.paragraphs" :key="paragraph">{{ paragraph }}</p>
            <a :href="article.url" target="_blank" rel="noopener noreferrer"
              >查看{{ article.source }}原文 <span aria-hidden="true">↗</span></a
            >
          </div>
          <details class="reader-analysis">
            <summary><strong>AI 解读</strong><span>示例</span></summary>
            <p>{{ article.readingNote }}</p>
          </details>
        </div>
      </article>
      <h1 v-else ref="heading" tabindex="-1">未找到这篇报道</h1>
    </main>
    <main v-else class="reader-wrap reader-secondary">
      <template v-if="route.name === 'calendar'">
        <h1 ref="heading" tabindex="-1">财经日历</h1>
        <p class="reader-caption">2024 年 10 月 · 样本日程</p>
        <h2>10 月 23 日</h2>
        <p>欧洲央行调整后的关键利率生效</p>
        <RouterLink :to="{ name: 'story', params: { id: 'ecb-rates' } }">阅读相关报道</RouterLink>
      </template>
      <template v-else>
        <h1 ref="heading" tabindex="-1">设置</h1>
        <p class="reader-caption">原型中的设置分组</p>
        <ul>
          <li>通知与提醒</li>
          <li>来源与采集</li>
          <li>AI 解读</li>
          <li>开发者功能</li>
        </ul>
      </template>
    </main>
  </div>
</template>
