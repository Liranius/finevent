import { createApp } from 'vue';

import App from './App.vue';
import { createAppRouter } from './router';

import './assets/main.css';
import './assets/calendar.css';
import './assets/news.css';

const router = createAppRouter();
const app = createApp(App).use(router);
void router.isReady().then(() => app.mount('#app'));
