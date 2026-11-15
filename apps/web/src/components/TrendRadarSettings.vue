<script setup lang="ts">
import { outlinedClose, outlinedHelpOutline } from '@quasar/extras/material-icons-outlined';
import { QBtn, QCard, QDialog } from 'quasar';
import { computed, onMounted, ref } from 'vue';

import { api, ApiError } from '../api';

type Section = 'sources' | 'keywords';
interface Settings {
  sources: { id: string; name: string; enabled: boolean }[];
  keywords: string;
  source_revision: string;
  keyword_revision: string;
  undo_sources: boolean;
  undo_keywords: boolean;
}
const emit = defineEmits<{ expired: [] }>();
const endpoint = 'news/trendradar-settings';
const base = ref<Settings | null>(null);
const selected = ref<string[]>([]);
const words = ref('');
const busy = ref(false);
const error = ref('');
const notice = ref('');
const helpOpen = ref(false);
const keywordExample = `[GLOBAL_FILTER]
广告

[WORD_GROUPS]
央行
降息

+芯片
出口
产能`;
const retry = ref<Section>();
const sourceChanged = computed(() =>
  base.value?.sources.some((s) => s.enabled !== selected.value.includes(s.id)),
);
const wordsChanged = computed(() => base.value !== null && words.value !== base.value.keywords);

function accept(result: Settings, section?: Section) {
  const keepSources = section === 'keywords' && sourceChanged.value;
  const keepWords = section === 'sources' && wordsChanged.value;
  if (base.value && keepSources) {
    result.sources = base.value.sources;
    result.source_revision = base.value.source_revision;
    result.undo_sources = base.value.undo_sources;
  } else selected.value = result.sources.filter((s) => s.enabled).map((s) => s.id);
  if (base.value && keepWords) {
    result.keywords = base.value.keywords;
    result.keyword_revision = base.value.keyword_revision;
    result.undo_keywords = base.value.undo_keywords;
  } else words.value = result.keywords;
  base.value = result;
}

async function request(section?: Section, mode: 'read' | 'save' | 'undo' = 'read') {
  if (busy.value) return;
  busy.value = true;
  error.value = '';
  notice.value = '';
  try {
    const payload =
      mode === 'read'
        ? undefined
        : {
            section,
            revision:
              section === 'sources' ? base.value?.source_revision : base.value?.keyword_revision,
            ...(mode === 'undo'
              ? { undo: true }
              : { value: section === 'sources' ? selected.value : words.value }),
          };
    accept(await api<Settings>(endpoint, payload ? 'PUT' : 'GET', payload), section);
    retry.value = undefined;
    if (mode !== 'read') {
      notice.value =
        (section === 'sources' ? '采集来源' : '日报关键词') +
        (mode === 'undo' ? '已恢复上一版' : '已保存') +
        '，等待下一轮采集。';
    }
  } catch (cause) {
    if (cause instanceof ApiError && cause.status === 401) emit('expired');
    error.value = cause instanceof Error ? cause.message : '配置读取失败。';
    retry.value = section;
  } finally {
    busy.value = false;
  }
}
onMounted(() => void request());
</script>

<template>
  <p v-if="error" role="alert" class="message error">
    {{ error }}
    <button class="quiet" :disabled="busy" @click="request(retry)">
      重新读取{{ retry === 'sources' ? '来源' : retry === 'keywords' ? '关键词' : '配置' }}
    </button>
  </p>
  <p v-if="notice" role="status" class="message">{{ notice }}</p>
  <p v-if="busy && !base" role="status">正在读取 TrendRadar 配置…</p>
  <div v-if="base" class="trend-settings">
    <form @submit.prevent="request('sources', 'save')">
      <fieldset :disabled="busy">
        <legend>采集来源</legend>
        <p class="caption">
          已启用 {{ selected.length }} /
          {{ base.sources.length }} 个。停用后保留已有报道和故障记录。
        </p>
        <label v-for="s in base.sources" :key="s.id" class="trend-source-option">
          <input v-model="selected" type="checkbox" :value="s.id" />{{ s.name }}
        </label>
        <div class="trend-actions">
          <button :disabled="!sourceChanged">保存来源</button>
          <button
            type="button"
            class="quiet"
            :disabled="!base.undo_sources || sourceChanged"
            @click="request('sources', 'undo')"
          >
            恢复上一版来源
          </button>
        </div>
      </fieldset>
    </form>
    <form @submit.prevent="request('keywords', 'save')">
      <fieldset :disabled="busy">
        <legend>日报关键词</legend>
        <p id="trend-keyword-help" class="caption">
          按标题筛选进入 TrendRadar 日报的报道。每行一个词，空行分组。
        </p>
        <div class="keyword-help-label">
          <label for="trend-keywords">关键词规则</label>
          <QBtn
            type="button"
            class="keyword-help-button"
            flat
            round
            :icon="outlinedHelpOutline"
            aria-label="日报关键词配置说明"
            aria-haspopup="dialog"
            @click="helpOpen = true"
          />
        </div>
        <textarea
          id="trend-keywords"
          v-model="words"
          rows="12"
          spellcheck="false"
          aria-describedby="trend-keyword-help"
        ></textarea>
        <div class="trend-actions">
          <button :disabled="!wordsChanged">保存关键词</button>
          <button
            type="button"
            class="quiet"
            :disabled="!base.undo_keywords || wordsChanged"
            @click="request('keywords', 'undo')"
          >
            恢复上一版关键词
          </button>
        </div>
      </fieldset>
    </form>
  </div>
  <QDialog v-model="helpOpen" aria-labelledby="keyword-help-heading">
    <QCard flat class="keyword-help-card">
      <header>
        <h2 id="keyword-help-heading">日报关键词怎么写</h2>
        <QBtn
          class="keyword-help-button"
          flat
          round
          :icon="outlinedClose"
          aria-label="关闭说明"
          autofocus
          @click="helpOpen = false"
        />
      </header>
      <p>
        在 <code>[WORD_GROUPS]</code> 下写想关注的词，每行一个。同组普通词命中任意一个即可；
        用空行另起一组。词前加 <code>+</code> 表示该组还必须包含这个词。
      </p>
      <p>
        要从所有组排除某个词，把它写在 <code>[GLOBAL_FILTER]</code> 下，每行一个。 以
        <code>#</code> 开头的行是注释。
      </p>
      <h3>示例</h3>
      <pre><code>{{ keywordExample }}</code></pre>
      <p>
        第一组关注“央行”或“降息”。第二组关注芯片，标题还须包含“出口”或“产能”。
        含“广告”的标题会从两组中排除。
      </p>
    </QCard>
  </QDialog>
</template>
