<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue';
import { RouterView, useRoute, useRouter } from 'vue-router';

import { api, ApiError, vapidBytes, type Status } from './api';
import { pushDeadline } from './push-timeout';
import type { NewsNavigation, CalendarNavigation } from './workspace-navigation';

const authenticated = ref(false);
const initialized = ref(false);
const password = ref('');
const status = ref<Status | null>(null);
const error = ref('');
const notice = ref('');
const pushPhase = ref('');
const refreshError = ref('');
let refreshing = false;
let pendingSubscription: Promise<PushSubscription> | null = null;
const busy = ref(false);
const route = useRoute();
const router = useRouter();
const workspace = computed(() => route.meta.workspace);
const newsNavigation = ref<NewsNavigation>();
const calendarNavigation = ref<CalendarNavigation>();
const deviceLabel = ref('');
const deviceID = ref<number | null>(null);
const scenario = ref('locked');
const delay = ref(30);
const registration = ref<ServiceWorkerRegistration | null>(null);
const updateReady = ref(false);
const pushSupported =
  'serviceWorker' in navigator &&
  'PushManager' in window &&
  'Notification' in window &&
  window.isSecureContext;
let timer: ReturnType<typeof setInterval> | undefined;

async function action(work: () => Promise<void>) {
  error.value = '';
  notice.value = '';
  busy.value = true;
  try {
    await work();
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '操作失败';
    if (cause instanceof ApiError && cause.status === 401) authenticated.value = false;
  } finally {
    busy.value = false;
  }
}
async function refresh() {
  if (!authenticated.value) return;
  const next = await api<Status>('status');
  status.value = next;
  refreshError.value = '';
  if (
    deviceID.value &&
    !next.devices.some((device) => device.id === deviceID.value && device.active)
  )
    deviceID.value = null;
}
async function login() {
  await action(async () => {
    await api('login', 'POST', { password: password.value });
    password.value = '';
    newsNavigation.value = undefined;
    calendarNavigation.value = undefined;
    authenticated.value = true;
    await refresh();
  });
}
async function logout() {
  await action(async () => {
    await api('logout', 'POST');
    authenticated.value = false;
    status.value = null;
  });
}
async function backgroundRefresh() {
  if (busy.value || refreshing || !authenticated.value) return;
  refreshing = true;
  try {
    await refresh();
  } catch (cause) {
    refreshError.value = cause instanceof Error ? cause.message : '自动刷新失败';
    if (cause instanceof ApiError && cause.status === 401) authenticated.value = false;
  } finally {
    refreshing = false;
  }
}
async function enablePush() {
  await action(async () => {
    if (!pushSupported || !status.value)
      throw new Error('当前浏览器不支持 Web Push，请使用 HTTPS 下的 Chrome。');
    pushPhase.value = '正在等待浏览器通知权限…';
    const permission = await pushDeadline(
      Notification.requestPermission(),
      90000,
      '等待通知权限超时，请确认浏览器提示后重试。',
    );
    if (permission !== 'granted')
      throw new Error('通知权限尚未开启，请在浏览器站点设置中允许通知。');
    pushPhase.value = '正在准备通知服务…';
    const ready = await pushDeadline(
      navigator.serviceWorker.ready,
      10000,
      '通知服务尚未就绪，请刷新后重试。',
    );
    pushPhase.value = '正在连接浏览器推送服务，最多等待 45 秒…';
    if (!pendingSubscription) {
      pendingSubscription = ready.pushManager
        .getSubscription()
        .then(
          (existing) =>
            existing ??
            ready.pushManager.subscribe({
              userVisibleOnly: true,
              applicationServerKey: vapidBytes(status.value!.vapid_public_key),
            }),
        )
        .finally(() => {
          pendingSubscription = null;
        });
    }
    const subscription = await pushDeadline(
      pendingSubscription,
      45000,
      '连接浏览器推送服务超时。请检查网络后重试；后台连接可能仍在继续，重试会复用该连接。',
    );
    pushPhase.value = '正在登记这台设备…';
    const result = await api<{ id: number }>('push/subscriptions', 'POST', {
      label: deviceLabel.value.trim(),
      subscription: { endpoint: subscription.endpoint, keys: subscription.toJSON().keys },
    });
    deviceID.value = result.id;
    notice.value = '设备订阅已登记。请发送测试，确认后台显示情况。';
    await refresh();
  });
  pushPhase.value = '';
}
async function queueTest() {
  await action(async () => {
    await api('push/tests', 'POST', {
      device_id: deviceID.value,
      scenario: scenario.value,
      delay_seconds: delay.value,
    });
    notice.value = `测试已排队，将在 ${delay.value} 秒后发送。请按所选场景操作设备。`;
    await refresh();
  });
}
async function record(id: number, result: string) {
  await action(async () => {
    await api(`push/results/${id}`, 'POST', { result });
    await refresh();
    notice.value = '实际观察结果已保存。';
  });
}
async function disableDevice(id: number) {
  await action(async () => {
    await api(`push/subscriptions/${id}`, 'DELETE');
    await refresh();
    notice.value = '该设备已停止接收后续通知。';
  });
}
function onVisible() {
  if (document.visibilityState === 'visible') {
    registration.value?.active?.postMessage('FLUSH_RECEIPTS');
    void backgroundRefresh();
  }
}
function applyUpdate() {
  registration.value?.waiting?.postMessage('SKIP_WAITING');
}
function reload() {
  if (updateReady.value) window.location.reload();
}
function navigateWorkspace(next: 'calendar' | 'news' | 'ai' | 'verification') {
  void router.push(
    next === 'verification' ? { name: next, params: { tab: 'push' } } : { name: next },
  );
}

onMounted(async () => {
  if (pushSupported && import.meta.env.PROD) {
    try {
      const worker = await navigator.serviceWorker.register('/finevent/sw.js', {
        scope: '/finevent/',
      });
      registration.value = worker;
      updateReady.value = !!worker.waiting;
      worker.addEventListener('updatefound', () => {
        const installing = worker.installing;
        installing?.addEventListener('statechange', () => {
          if (installing.state === 'installed' && navigator.serviceWorker.controller)
            updateReady.value = true;
        });
      });
      navigator.serviceWorker.addEventListener('controllerchange', reload);
      worker.active?.postMessage('FLUSH_RECEIPTS');
    } catch {
      error.value = '应用离线资源未准备好，通知功能可能不可用。';
    }
  }
  await action(async () => {
    const session = await api<{ authenticated: boolean }>('session');
    authenticated.value = session.authenticated;
    await refresh();
  });
  initialized.value = true;
  timer = setInterval(() => {
    if (!busy.value && document.visibilityState === 'visible') void backgroundRefresh();
  }, 30000);
  document.addEventListener('visibilitychange', onVisible);
  window.addEventListener('online', onVisible);
});
onUnmounted(() => {
  clearInterval(timer);
  document.removeEventListener('visibilitychange', onVisible);
  window.removeEventListener('online', onVisible);
  if ('serviceWorker' in navigator)
    navigator.serviceWorker.removeEventListener('controllerchange', reload);
});
</script>

<template>
  <div class="workspace">
    <header class="masthead">
      <a class="brand" href="/finevent/"
        ><img :src="'/finevent/icon.svg'" width="32" height="32" alt="" />finevent<span
          >财经事件</span
        ></a
      >
      <div class="header-actions">
        <span class="phase">财经事件 · 个人工作台</span
        ><button v-if="authenticated" class="quiet" :disabled="busy" @click="logout">退出</button>
      </div>
    </header>
    <main>
      <div v-if="error" class="message error" role="alert">{{ error }}</div>
      <div v-if="refreshError" class="message error" role="alert">
        自动刷新失败：{{ refreshError }} <button @click="backgroundRefresh">重试状态刷新</button>
      </div>
      <div v-if="notice" class="message" role="status">{{ notice }}</div>
      <div v-if="updateReady" class="message">
        新版本已准备好。<button class="quiet" @click="applyUpdate">更新应用</button>
      </div>
      <p v-if="!initialized" class="empty" role="status">正在连接工作台…</p>
      <section v-else-if="!authenticated" class="login-panel" aria-labelledby="login-title">
        <span class="eyebrow">PERSONAL EVENT DESK</span>
        <h1 id="login-title">你的财经事件工作台</h1>
        <p class="intro">将重要公告与开放窗口，<br />提前安排进你的日程。</p>
        <form @submit.prevent="login">
          <label for="password">访问密码</label
          ><input
            id="password"
            v-model="password"
            type="password"
            autocomplete="current-password"
            required
            maxlength="72"
          /><button class="primary" :disabled="busy">
            {{ busy ? '正在登录…' : '进入工作台' }}
          </button>
        </form>
        <p class="caption">使用服务器初始化时设置的密码。</p>
      </section>
      <template v-else>
        <nav class="section-nav workspace-nav" aria-label="工作区">
          <button
            :aria-current="workspace === 'calendar' ? 'page' : undefined"
            @click="navigateWorkspace('calendar')"
          >
            财经日历
          </button>
          <button
            :aria-current="workspace === 'news' ? 'page' : undefined"
            @click="navigateWorkspace('news')"
          >
            新闻事件流
          </button>
          <button
            :aria-current="workspace === 'ai' ? 'page' : undefined"
            @click="navigateWorkspace('ai')"
          >
            AI 解读
          </button>
          <button
            :aria-current="workspace === 'verification' ? 'page' : undefined"
            @click="navigateWorkspace('verification')"
          >
            验证工作台
          </button>
        </nav>
        <RouterView v-slot="{ Component }">
          <component
            :is="Component"
            v-if="workspace === 'calendar'"
            :navigation="calendarNavigation"
            @remember="calendarNavigation = $event"
            @expired="authenticated = false"
            @devices="navigateWorkspace('verification')"
          />
          <component
            :is="Component"
            v-else-if="workspace === 'news'"
            :navigation="newsNavigation"
            @remember="newsNavigation = $event"
            @expired="authenticated = false"
            @devices="navigateWorkspace('verification')"
          />
          <component
            :is="Component"
            v-else-if="workspace === 'ai'"
            @expired="authenticated = false"
          />
          <component
            :is="Component"
            v-else-if="workspace === 'verification'"
            v-model:device-label="deviceLabel"
            v-model:device-id="deviceID"
            v-model:scenario="scenario"
            v-model:delay="delay"
            :status="status"
            :busy="busy"
            :push-supported="pushSupported"
            :push-phase="pushPhase"
            @refresh="action(refresh)"
            @enable-push="enablePush"
            @queue-test="queueTest"
            @record="record"
            @disable-device="disableDevice"
            @expired="authenticated = false"
          />
          <component :is="Component" v-else />
        </RouterView>
      </template>
    </main>
  </div>
</template>
