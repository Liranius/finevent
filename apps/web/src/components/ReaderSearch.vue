<script setup lang="ts">
import {
  outlinedClose,
  outlinedExpandMore,
  outlinedSearch,
  outlinedTune,
} from '@quasar/extras/material-icons-outlined';
import { QBtn, QCard, QDialog, QInput, QMenu, QSelect, useQuasar } from 'quasar';
import { computed, nextTick, ref } from 'vue';

import { defaults, type searchFields, type FilterKey, type ReaderFilters } from '../news-search';

const props = defineProps<{ fields: ReturnType<typeof searchFields>; placeholder: string }>();
const emit = defineEmits<{ submit: [] }>();

const applied = defineModel<ReaderFilters>({ required: true });
const search = defineModel<string>('search', { required: true });
const draft = ref<ReaderFilters>({ ...applied.value });
const open = ref(false);
const $q = useQuasar();
const narrow = computed(() => $q.screen.width <= 700);
const active = computed(() =>
  props.fields
    .filter((f) => applied.value[f.key] !== defaults[f.key])
    .map((f) => ({
      key: f.key,
      label: f.label,
      value: f.options.find((o) => o.value === applied.value[f.key])?.label ?? applied.value[f.key],
    })),
);

function edit() {
  draft.value = { ...applied.value };
  open.value = true;
}
function apply() {
  applied.value = { ...draft.value };
  open.value = false;
}
function remove(key: FilterKey) {
  applied.value = { ...applied.value, [key]: defaults[key] };
  void nextTick(focusTrigger);
}
function focusTrigger() {
  document.getElementById('reader-filter-trigger')?.focus({ preventScroll: true });
}
function submitSearch(event: KeyboardEvent) {
  if (event.isComposing) return;
  event.preventDefault();
  emit('submit');
}
</script>

<template>
  <div class="reader-search-area">
    <QInput
      v-model="search"
      type="search"
      outlined
      aria-label="标题或正文"
      maxlength="100"
      enterkeyhint="search"
      :placeholder="placeholder"
      @keydown.enter="submitSearch"
    >
      <template #prepend
        ><QBtn
          flat
          round
          dense
          :icon="outlinedSearch"
          aria-label="搜索报道"
          @click="emit('submit')"
      /></template>
      <template #append>
        <QBtn
          id="reader-filter-trigger"
          class="reader-filter-trigger"
          flat
          dense
          no-caps
          :icon="outlinedTune"
          label="高级搜索"
          aria-haspopup="dialog"
          :aria-expanded="open"
          @click="edit"
        />
      </template>
    </QInput>
    <div v-if="active.length" class="reader-conditions" aria-label="当前筛选条件">
      <QBtn
        v-for="item in active"
        :key="item.key"
        flat
        dense
        no-caps
        :icon-right="outlinedClose"
        :label="item.value"
        :aria-label="'移除' + item.label + '条件：' + item.value"
        @click="remove(item.key)"
      />
    </div>
    <component
      :is="narrow ? QDialog : QMenu"
      v-model="open"
      v-bind="
        narrow
          ? { position: 'bottom' as const }
          : {
              target: '#reader-filter-trigger',
              noParentEvent: true,
              anchor: 'bottom right' as const,
              self: 'top right' as const,
              offset: [0, 8],
            }
      "
      role="dialog"
      aria-labelledby="advanced-heading"
      @hide="focusTrigger"
    >
      <QCard flat>
        <form class="reader-advanced" aria-label="报道高级搜索" @submit.prevent="apply">
          <header class="advanced-head">
            <h2 id="advanced-heading">高级搜索</h2>
            <QBtn
              flat
              round
              dense
              :icon="outlinedClose"
              aria-label="关闭高级搜索"
              @click="open = false"
            />
          </header>
          <div class="advanced-fields">
            <QSelect
              v-for="field in fields"
              :key="field.key"
              v-model="draft[field.key]"
              outlined
              options-dense
              emit-value
              map-options
              behavior="menu"
              :label="field.label"
              :options="field.options"
              :dropdown-icon="outlinedExpandMore"
            />
          </div>
          <p v-if="$slots.default" class="advanced-note"><slot /></p>
          <div class="advanced-actions">
            <QBtn flat no-caps label="重置条件" @click="draft = { ...defaults }" />
            <QBtn flat no-caps label="取消" @click="open = false" />
            <QBtn
              unelevated
              no-caps
              color="primary"
              text-color="on-primary"
              label="应用筛选"
              type="submit"
            />
          </div>
        </form>
      </QCard>
    </component>
  </div>
</template>
