<script setup lang="ts">
import {
  outlinedCalendarMonth,
  outlinedNewspaper,
  outlinedSettings,
} from '@quasar/extras/material-icons-outlined';
import { QBtn } from 'quasar';

defineProps<{ active?: string | symbol | null }>();
const emit = defineEmits<{ navigate: [event: Event, name: string] }>();
const destinations = [
  { name: 'news', label: '报道', icon: outlinedNewspaper },
  { name: 'calendar', label: '财经日历', icon: outlinedCalendarMonth },
  { name: 'settings', label: '设置', icon: outlinedSettings },
] as const;
</script>
<template>
  <nav aria-label="主导航" class="reader-nav">
    <QBtn
      v-for="dest in destinations"
      :key="dest.name"
      flat
      no-caps
      stack
      :to="{ name: dest.name }"
      :icon="dest.icon"
      :label="dest.label"
      :class="{ 'reader-current': active === dest.name }"
      :aria-current="active === dest.name ? 'page' : undefined"
      @click="emit('navigate', $event, dest.name)"
    />
  </nav>
</template>
