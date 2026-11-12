<script setup lang="ts">
import {
  outlinedAutoStories,
  outlinedBrightness6,
  outlinedChevronRight,
  outlinedNotificationsNone,
  outlinedRssFeed,
  outlinedTerminal,
} from '@quasar/extras/material-icons-outlined';
import { QBtnToggle, QIcon, QItem, QItemLabel, QItemSection, QList, useQuasar } from 'quasar';
import { onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';

import { setAppearance } from '../appearance';

const router = useRouter();
const $q = useQuasar();
const heading = ref<HTMLElement>();
const appearances = [
  { label: '跟随系统', value: 'auto' },
  { label: '日间', value: false },
  { label: '夜间', value: true },
];
const groups = [
  {
    title: '通知与提醒',
    icon: outlinedNotificationsNone,
    entries: [
      {
        label: '报道关注与推送',
        description: '选择关注类别、行业和接收设备',
        to: { name: 'news-push' },
      },
      {
        label: '报道通知记录',
        description: '查看推送服务接受、设备回执和点击记录',
        to: { name: 'news-notifications' },
      },
      {
        label: '日历提醒设置',
        description: '设置提醒时间，查看待发送计划',
        to: { name: 'reminders' },
      },
      {
        label: '提醒记录',
        description: '查看日历提醒的送达状态，打开对应事件',
        to: { name: 'reminder-history' },
      },
    ],
  },
  {
    title: '来源状态',
    icon: outlinedRssFeed,
    entries: [
      {
        label: '采集通道状态',
        description: '查看报道来源的最近采集、失败与缺口',
        to: { name: 'news-channels' },
      },
      {
        label: '日历官方来源',
        description: '核对同步时间、覆盖日期与官方快照',
        to: { name: 'calendar-sources' },
      },
    ],
  },
  {
    title: 'AI 解读',
    icon: outlinedAutoStories,
    entries: [
      {
        label: 'AI 设置与预算',
        description: '调整解读范围，查看月度预算和用量',
        to: { name: 'ai' },
      },
      {
        label: 'AI 解读记录',
        description: '查看历次解读、任务状态和引用快照',
        to: { name: 'ai-history' },
      },
    ],
  },
  {
    title: '开发者功能',
    icon: outlinedTerminal,
    entries: [
      {
        label: '设备、测试通知与诊断',
        description: '连接设备，验证通知送达、采集时效和资源占用',
        to: { name: 'verification', params: { tab: 'push' } },
      },
    ],
  },
];

function rememberEntry(event: Event, name: string) {
  if (
    !(event instanceof MouseEvent) ||
    event.button !== 0 ||
    event.altKey ||
    event.ctrlKey ||
    event.metaKey ||
    event.shiftKey
  )
    return;
  router.options.history.replace(router.currentRoute.value.fullPath, {
    settingsEntry: name,
    settingsScroll: window.scrollY,
  });
}
onMounted(() => {
  const { settingsEntry, settingsScroll } = router.options.history.state;
  const entry =
    typeof settingsEntry === 'string' ? document.getElementById('setting-' + settingsEntry) : null;
  if (entry && typeof settingsScroll === 'number') {
    window.scrollTo(0, settingsScroll);
    entry.focus({ preventScroll: true });
  } else {
    window.scrollTo(0, 0);
    heading.value?.focus({ preventScroll: true });
  }
});
</script>
<template>
  <section class="settings-index" aria-labelledby="settings-title">
    <h1 id="settings-title" ref="heading" tabindex="-1">设置</h1>
    <section class="settings-group" aria-labelledby="appearance-heading">
      <h2 id="appearance-heading"><QIcon :name="outlinedBrightness6" aria-hidden="true" />外观</h2>
      <QBtnToggle
        class="appearance-options"
        role="group"
        aria-labelledby="appearance-heading"
        flat
        no-caps
        :model-value="$q.dark.mode"
        :options="appearances"
        @update:model-value="setAppearance"
      />
    </section>
    <section
      v-for="group in groups"
      :key="group.title"
      class="settings-group"
      :aria-label="group.title"
    >
      <h2><QIcon :name="group.icon" aria-hidden="true" />{{ group.title }}</h2>
      <QList role="presentation" class="settings-entries">
        <QItem
          v-for="entry in group.entries"
          :id="'setting-' + entry.to.name"
          :key="entry.to.name"
          :to="entry.to"
          :aria-labelledby="'label-' + entry.to.name"
          :aria-describedby="'hint-' + entry.to.name"
          @click="rememberEntry($event, entry.to.name)"
        >
          <QItemSection>
            <QItemLabel :id="'label-' + entry.to.name" class="settings-label">{{
              entry.label
            }}</QItemLabel>
            <QItemLabel :id="'hint-' + entry.to.name" class="settings-hint" caption>{{
              entry.description
            }}</QItemLabel>
          </QItemSection>
          <QItemSection class="settings-chevron" side
            ><QIcon :name="outlinedChevronRight" size="18px" aria-hidden="true"
          /></QItemSection>
        </QItem>
      </QList>
    </section>
  </section>
</template>
