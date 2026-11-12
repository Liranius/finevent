import { Quasar } from 'quasar';
import zhCN from 'quasar/lang/zh-CN';
import { createApp, h } from 'vue';
import { createRouter, createWebHashHistory, RouterView } from 'vue-router';

import NewsReader from './NewsReader.vue';

import './reader.css';

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', redirect: { name: 'news' } },
    { path: '/news', name: 'news', component: NewsReader },
    { path: '/news/:id', name: 'story', component: NewsReader },
    { path: '/calendar', name: 'calendar', component: NewsReader },
    { path: '/settings', name: 'settings', component: NewsReader },
    { path: '/:pathMatch(.*)*', redirect: { name: 'news' } },
  ],
});
window.history.scrollRestoration = 'manual';
const app = createApp({ render: () => h(RouterView) })
  .use(Quasar, { lang: zhCN })
  .use(router);
void router.isReady().then(() => app.mount('#app'));
