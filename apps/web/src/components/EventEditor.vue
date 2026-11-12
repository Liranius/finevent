<script setup lang="ts">
import { ref } from 'vue';

import { type CalendarEvent } from '../calendar';

const props = defineProps<{
  event: CalendarEvent;
  busy: boolean;
  fund?: boolean;
  confirm?: boolean;
}>();
const emit = defineEmits<{ save: [event: CalendarEvent]; cancel: [] }>();
const form = ref({ ...props.event });
const acknowledged = ref(false);
const local = (value: string) => value.slice(0, 16);
const start = ref(
  props.event.precision === 'minute' ? local(props.event.start) : props.event.start,
);
const end = ref(props.event.precision === 'minute' ? local(props.event.end) : props.event.end);
function precisionChanged() {
  start.value = '';
  end.value = '';
}
function submit() {
  emit('save', {
    ...form.value,
    start: start.value + (form.value.precision === 'minute' ? ':00+08:00' : ''),
    end: end.value + (form.value.precision === 'minute' ? ':00+08:00' : ''),
    timezone: 'Asia/Shanghai',
  });
}
</script>

<template>
  <form class="event-editor" @submit.prevent="submit">
    <label for="event-title">事件名称</label>
    <input id="event-title" v-model="form.title" required maxlength="160" />
    <template v-if="fund">
      <label for="fund-code">基金代码</label>
      <input
        id="fund-code"
        v-model="form.fund_code"
        required
        pattern="[0-9]{6}"
        inputmode="numeric"
        maxlength="6"
        placeholder="六位代码，每个份额单独录入"
      />
    </template>
    <template v-else>
      <label for="event-precision">时间精度</label>
      <select id="event-precision" v-model="form.precision" @change="precisionChanged">
        <option value="date">仅日期（不补造时刻）</option>
        <option value="minute">明确时刻 · 北京时间</option>
      </select>
    </template>
    <div class="form-row">
      <div>
        <label for="event-start">开始{{ form.precision === 'date' ? '日期' : '时间' }}</label
        ><input
          id="event-start"
          v-model="start"
          :type="form.precision === 'date' ? 'date' : 'datetime-local'"
          required
        />
      </div>
      <div>
        <label for="event-end">结束{{ form.precision === 'date' ? '日期' : '时间' }}</label
        ><input
          id="event-end"
          v-model="end"
          :min="start"
          :type="form.precision === 'date' ? 'date' : 'datetime-local'"
          required
        />
      </div>
    </div>
    <p class="caption">
      单日事件填同一天；日期区间包含结束日。基金交易日、时间及渠道限制请在下方保留。
    </p>
    <div class="form-row">
      <div>
        <label for="event-importance">重要性</label
        ><select id="event-importance" v-model="form.importance">
          <option :value="1">一般</option>
          <option :value="2">关注</option>
          <option :value="3">重要</option>
        </select>
      </div>
      <div v-if="!confirm">
        <label for="event-status">确定程度</label
        ><select id="event-status" v-model="form.status">
          <option value="scheduled">预定</option>
          <option value="confirmed">已确认</option>
          <option v-if="event.id" value="rescheduled">已改期</option>
          <option v-if="event.id" value="cancelled">已取消</option>
        </select>
      </div>
    </div>
    <template v-if="!fund && !event.id"
      ><label for="event-url">官方原文链接</label
      ><input id="event-url" v-model="form.url" type="url" required maxlength="2048" /><label
        for="event-evidence"
        >原文依据</label
      ><textarea id="event-evidence" v-model="form.evidence" rows="3" maxlength="3000" required />
    </template>
    <label for="event-conditions">{{ fund ? '开放业务及限制条件' : '备注与成立条件' }}</label>
    <textarea
      id="event-conditions"
      v-model="form.conditions"
      rows="4"
      maxlength="3000"
      :required="fund"
      :placeholder="
        fund
          ? '核对申购/赎回、交易日、金额、渠道、份额等条件；无额外限制时明确注明。'
          : '补充公告中的限定条件'
      "
    />
    <label v-if="confirm" class="check-label"
      ><input
        v-model="acknowledged"
        type="checkbox"
        required
      />我已核对公告原文、基金代码、开放区间和限制条件</label
    >
    <div class="record-actions">
      <button class="primary" :disabled="busy || (confirm && !acknowledged)">
        {{ busy ? '正在保存…' : confirm ? '确认入历' : '保存事件' }}</button
      ><button type="button" :disabled="busy" @click="emit('cancel')">取消编辑</button>
    </div>
  </form>
</template>
