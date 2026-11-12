<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { RouterLink } from 'vue-router';

const heading = ref<HTMLElement | null>(null);
onMounted(() => heading.value?.focus());
</script>

<template>
  <section class="primary-panel">
    <h1 ref="heading" tabindex="-1">找不到这个页面</h1>
    <p>链接无效或页面地址不存在，请检查链接或返回财经日历。</p>
    <RouterLink :to="{ name: 'calendar' }">返回财经日历</RouterLink>
  </section>
</template>
