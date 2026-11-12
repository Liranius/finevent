<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue';
import { useRoute } from 'vue-router';

import { api, ApiError } from '../api';
import { newsTime, sourceName, type NewsMeta } from '../news';
import NewsPushSettings from './NewsPushSettings.vue';
import SourceHealth from './SourceHealth.vue';
const emit = defineEmits<{ expired: []; devices: [] }>();
const route = useRoute();
const channels = computed(() => route.name === 'news-channels');
const records = computed(() => route.name === 'news-notifications');
const heading = ref<HTMLElement>();
const meta = ref<NewsMeta>();
const error = ref('');
let request = 0;
async function load() {
  const current = ++request;
  try {
    const next = await api<NewsMeta>('news/meta');
    if (current === request) {
      meta.value = next;
      error.value = '';
    }
  } catch (cause) {
    if (current !== request) return;
    if (cause instanceof ApiError && cause.status === 401) emit('expired');
    error.value = cause instanceof Error ? cause.message : '采集通道读取失败';
  }
}
function loadWhenVisible() {
  if (document.visibilityState === 'visible') void load();
}
watch(
  () => route.name,
  (_, __, cleanup) => {
    void nextTick(() => heading.value?.focus());
    if (!channels.value) return;
    void load();
    const timer = setInterval(loadWhenVisible, 30000);
    document.addEventListener('visibilitychange', loadWhenVisible);
    cleanup(() => {
      clearInterval(timer);
      document.removeEventListener('visibilitychange', loadWhenVisible);
      ++request;
    });
  },
  { immediate: true },
);
</script>
<template>
  <section>
    <h1 id="news-settings-heading" ref="heading" tabindex="-1">
      {{ channels ? '采集通道状态' : records ? '报道通知记录' : '报道关注与推送' }}
    </h1>
    <NewsPushSettings
      v-if="!channels"
      :records-only="records"
      @expired="emit('expired')"
      @devices="emit('devices')"
    />
    <template v-else>
      <p v-if="error" class="message error" role="alert">
        {{ error }} <button @click="load">重试</button>
      </p>
      <section v-if="meta" class="news-channels">
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
        <SourceHealth @refresh="load" @expired="emit('expired')" />
      </section>
    </template>
  </section>
</template>
