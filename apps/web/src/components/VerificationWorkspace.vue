<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { deliveryState, formatTime, type Status } from '../api';
import SourceHealth from './SourceHealth.vue';

const props = defineProps<{
  status: Status | null;
  busy: boolean;
  pushSupported: boolean;
  pushPhase: string;
}>();
const emit = defineEmits<{
  refresh: [];
  enablePush: [];
  queueTest: [];
  record: [id: number, result: string];
  disableDevice: [id: number];
  expired: [];
}>();
const deviceLabel = defineModel<string>('deviceLabel', { required: true });
const deviceID = defineModel<number | null>('deviceId', { required: true });
const scenario = defineModel<string>('scenario', { required: true });
const delay = defineModel<number>('delay', { required: true });
const route = useRoute();
const router = useRouter();
const view = computed(() => route.params.tab);
const heading = ref<HTMLElement | null>(null);
let keepTabFocus = false;
function navigateTab(tab: string) {
  if (tab === view.value) return;
  keepTabFocus = true;
  void router.push({ name: 'verification', params: { tab } });
}
watch(
  () => route.params.tab,
  async () => {
    const keepFocus = keepTabFocus;
    keepTabFocus = false;
    await nextTick();
    if (!keepFocus) heading.value?.focus();
  },
);
onMounted(() => heading.value?.focus());
const scenarios = [
  { value: 'foreground', label: '前台打开' },
  { value: 'background', label: '切到后台' },
  { value: 'locked', label: '手机锁屏' },
  { value: 'network_switch', label: '切换网络' },
  { value: 'reconnect', label: '断网后恢复' },
];
const resource = computed(() => props.status?.resources[0]);
const activeDevices = computed(() => props.status?.devices.filter((device) => device.active) ?? []);
function mb(bytes: number | undefined) {
  return bytes ? `${(bytes / 1024 / 1024).toFixed(1)} MiB` : '待采样';
}
</script>

<template>
  <div class="page-heading">
    <div>
      <span class="eyebrow">VERIFICATION LOG</span>
      <h1 ref="heading" tabindex="-1">开发者功能</h1>
      <p class="intro">提醒送达、采集时效、资源共存。用实际记录确认。</p>
    </div>
    <div class="as-of">
      <span>最近刷新 · 北京时间</span><strong>{{ formatTime(status?.server_time ?? 0) }}</strong>
    </div>
  </div>
  <nav class="section-nav" aria-label="验证项目">
    <button
      v-for="tab in [
        { id: 'push', label: '01 通知送达' },
        { id: 'sources', label: '02 采集时效' },
        { id: 'resources', label: '03 资源共存' },
      ]"
      :key="tab.id"
      :aria-current="view === tab.id ? 'page' : undefined"
      @click="navigateTab(tab.id)"
    >
      {{ tab.label }}</button
    ><button class="refresh" :disabled="busy" @click="emit('refresh')">刷新记录</button>
  </nav>
  <section v-if="view === 'push'" aria-labelledby="push-title">
    <div class="split">
      <div class="primary-panel">
        <span class="step">第一步 / 设备</span>
        <h2 id="push-title">连接这台设备</h2>
        <p>在小米 Chrome 与电脑浏览器中分别启用，逐一验证。</p>
        <form @submit.prevent="emit('enablePush')">
          <label for="device-label">设备名称</label
          ><input
            id="device-label"
            v-model="deviceLabel"
            placeholder="例如：小米 13 Ultra · Chrome"
            required
            maxlength="60"
          /><button class="primary" :disabled="busy || !pushSupported">启用这台设备的通知</button>
        </form>
        <p v-if="pushPhase" class="caption" role="status">{{ pushPhase }}</p>
        <p v-if="!pushSupported" class="warning">
          当前环境不支持 Web Push。请使用 HTTPS 下支持通知的浏览器。
        </p>
        <p class="caption">订阅成功只表示链路已建立，锁屏显示还需要测试。</p>
      </div>
      <div class="secondary-panel">
        <span class="step">第二步 / 场景</span>
        <h2>安排一条测试提醒</h2>
        <form @submit.prevent="emit('queueTest')">
          <label for="device">接收设备</label
          ><select id="device" v-model="deviceID" required>
            <option :value="null" disabled>选择已启用的设备</option>
            <option v-for="device in activeDevices" :key="device.id" :value="device.id">
              {{ device.label }}
            </option>
          </select>
          <div class="form-row">
            <div>
              <label for="scenario">测试场景</label
              ><select id="scenario" v-model="scenario">
                <option v-for="item in scenarios" :key="item.value" :value="item.value">
                  {{ item.label }}
                </option>
              </select>
            </div>
            <div>
              <label for="delay">发送延迟</label
              ><select id="delay" v-model="delay">
                <option :value="0">立即</option>
                <option :value="30">30 秒</option>
                <option :value="60">60 秒</option>
                <option :value="120">120 秒</option>
              </select>
            </div>
          </div>
          <button :disabled="busy || !deviceID">发送测试提醒</button>
        </form>
        <p class="caption">选“手机锁屏”后锁住屏幕，收到时点击通知返回。消息有效期为 5 分钟。</p>
      </div>
    </div>
    <div class="section-title">
      <h2>送达记录</h2>
      <span>服务接受 ≠ 锁屏显示</span>
    </div>
    <p v-if="!status?.deliveries.length" class="empty">
      还没有测试记录。先启用设备，再安排一条提醒。
    </p>
    <ol v-else class="delivery-list">
      <li v-for="delivery in status.deliveries" :key="delivery.id">
        <div class="record-heading">
          <strong>#{{ delivery.id }} · {{ delivery.label }}</strong
          ><span
            class="status-text"
            :class="{ warning: ['failed', 'expired'].includes(delivery.status) }"
            >{{ deliveryState(delivery) }}</span
          >
        </div>
        <p class="caption">
          {{ scenarios.find((item) => item.value === delivery.scenario)?.label }} · 计划
          {{ formatTime(delivery.due) }}
        </p>
        <div class="evidence-chain">
          <span>已排队</span
          ><span :class="{ complete: delivery.accepted_at }"
            >服务接受 {{ delivery.accepted_at ? formatTime(delivery.accepted_at) : '—' }}</span
          ><span :class="{ complete: delivery.received_at }"
            >设备接收 {{ delivery.received_at ? formatTime(delivery.received_at) : '—' }}</span
          ><span :class="{ complete: delivery.clicked_at }"
            >已点击 {{ delivery.clicked_at ? formatTime(delivery.clicked_at) : '—' }}</span
          >
        </div>
        <p v-if="delivery.error" class="warning">{{ delivery.error }}</p>
        <div class="record-actions">
          <span
            >实际显示：{{
              delivery.manual_result === 'seen'
                ? '已看到通知'
                : delivery.manual_result === 'not_seen'
                  ? '未看到通知'
                  : '待记录'
            }}</span
          ><button
            :disabled="busy"
            :aria-label="`记录第 ${delivery.id} 条已看到通知`"
            @click="emit('record', delivery.id, 'seen')"
          >
            已看到</button
          ><button
            class="quiet"
            :disabled="busy"
            :aria-label="`记录第 ${delivery.id} 条未看到通知`"
            @click="emit('record', delivery.id, 'not_seen')"
          >
            未看到
          </button>
        </div>
      </li>
    </ol>
    <details v-if="activeDevices.length" class="device-details">
      <summary>管理已启用设备（{{ activeDevices.length }}）</summary>
      <div v-for="device in activeDevices" :key="device.id" class="record-actions">
        <span>{{ device.label }}</span
        ><button class="quiet" :disabled="busy" @click="emit('disableDevice', device.id)">
          停用
        </button>
      </div>
    </details>
  </section>
  <section v-else-if="view === 'sources'" aria-labelledby="sources-title">
    <SourceHealth @expired="emit('expired')" />
    <div class="section-title">
      <h2 id="sources-title">消息多久抵达？</h2>
      <span>目标 3–5 分钟 · 免费源</span>
    </div>
    <p class="intro">首次拉取的历史消息不计入时效。没有发布时间的来源不生成延迟数字。</p>
    <p v-if="status && !status.collection_enabled" class="message">
      当前服务未开启来源采样，部署时开启采集后开始记录。
    </p>
    <div v-if="status?.latencies.length" class="table-scroll">
      <table>
        <caption>
          近七天入库时效
        </caption>
        <thead>
          <tr>
            <th>来源</th>
            <th>有效样本</th>
            <th>P50</th>
            <th>P95</th>
            <th>5 分钟内</th>
            <th>发布时间未知</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="sample in status.latencies" :key="sample.source">
            <td>{{ sample.source }}</td>
            <td>{{ sample.samples }}</td>
            <td>{{ sample.samples ? `${sample.p50_seconds} 秒` : '—' }}</td>
            <td>{{ sample.samples ? `${sample.p95_seconds} 秒` : '—' }}</td>
            <td>
              {{ sample.samples ? `${Math.round(sample.within_5_minutes * 100)}%` : '—' }}
            </td>
            <td>{{ sample.unknown_publication }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <p v-else class="empty">尚无有效时效样本。需要持续观察两个交易日后评估。</p>
    <div v-for="coverage in status?.coverage" :key="coverage.source" class="coverage">
      <strong>{{ coverage.source }}</strong
      ><span>{{ coverage.successes }}/{{ coverage.polls }} 次成功 · {{ coverage.gaps }} 次缺口</span
      ><span>{{ formatTime(coverage.first_poll) }} — {{ formatTime(coverage.last_poll) }}</span>
    </div>
    <h2 class="subheading">采集记录</h2>
    <ul class="plain-list">
      <li v-for="poll in status?.polls" :key="poll.id">
        <div class="record-heading">
          <strong>{{ poll.source }}</strong
          ><span>{{ formatTime(poll.completed) }}</span>
        </div>
        <p :class="{ warning: !poll.success || poll.gap }">
          {{ poll.success ? `新增 ${poll.added} · 修订 ${poll.changed}` : poll.error }}
          {{ poll.gap }}
        </p>
      </li>
    </ul>
    <h2 class="subheading">最近发现</h2>
    <ul class="plain-list">
      <li v-for="item in status?.news" :key="item.id">
        <a v-if="item.url" :href="item.url" target="_blank" rel="noopener noreferrer">{{
          item.title
        }}</a
        ><strong v-else>{{ item.title }}</strong>
        <p class="caption">
          {{ item.source }} · 发布
          {{ item.published_at ? formatTime(item.published_at) : '未知' }} · 发现
          {{ formatTime(item.first_seen) }} <span v-if="item.baseline">· 启动历史样本</span>
        </p>
      </li>
    </ul>
  </section>
  <section v-else aria-labelledby="resources-title">
    <div class="section-title">
      <h2 id="resources-title">与 TrendRadar 一起运行</h2>
      <span>资源验证尚未签收</span>
    </div>
    <p class="intro">目标是在采集和通知运行时保持足够余量。Go 运行时内存与实际进程内存分别记录。</p>
    <dl class="resource-ledger">
      <div>
        <dt>进程常驻内存 RSS</dt>
        <dd>{{ mb(resource?.rss) }}</dd>
      </div>
      <div>
        <dt>Go 运行时申请内存</dt>
        <dd>{{ mb(resource?.go_sys) }}</dd>
      </div>
      <div>
        <dt>服务器可用内存</dt>
        <dd>{{ mb(resource?.available) }}</dd>
      </div>
      <div>
        <dt>服务内存峰值（cgroup）</dt>
        <dd>{{ mb(resource?.cgroup_peak) }}</dd>
      </div>
      <div>
        <dt>OOM 终止次数</dt>
        <dd>{{ resource?.cgroup_peak ? resource.oom_kills : '待 Linux 服务采样' }}</dd>
      </div>
    </dl>
    <div class="message">
      AI 并发验证以服务器验收报告为准。此处展示已运行任务的资源采样，完整业务负载仍待后续验证。
    </div>
    <p class="caption">
      采样 {{ formatTime(resource?.at ?? 0) }} · 生产服务内存上限 96 MiB；Windows 开发环境不提供
      Linux 指标。
    </p>
  </section>
  <footer>当前数据均来自实际采样，空白表示尚未验证。日历在“财经日历”工作区查看。</footer>
</template>
