import { Dark, Quasar } from 'quasar';
import zhCN from 'quasar/lang/zh-CN';
import { createApp, watch } from 'vue';

import App from './App.vue';
import { savedAppearance } from './appearance';
import { createAppRouter } from './router';

import 'quasar/dist/quasar.prod.css';
import './assets/main.css';
import './assets/reader-search.css';
import './assets/reader-nav.css';
import './assets/settings.css';
import './assets/calendar.css';
import './assets/news.css';

const router = createAppRouter();
const app = createApp(App)
  .use(Quasar, { lang: zhCN, config: { dark: savedAppearance() } })
  .use(router);
watch(
  () => Dark.isActive,
  (dark) => {
    document.documentElement.dataset.theme = dark ? 'dark' : 'light';
    const paper = getComputedStyle(document.documentElement).getPropertyValue('--paper').trim();
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', paper);
  },
  { immediate: true },
);
void router.isReady().then(() => app.mount('#app'));
