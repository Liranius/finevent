import { createRouter, createWebHashHistory, type RouterHistory } from 'vue-router';

import AISettings from './components/AISettings.vue';
import CalendarWorkspace from './components/CalendarWorkspace.vue';
import NewsWorkspace from './components/NewsWorkspace.vue';
import NotFound from './components/NotFound.vue';
import VerificationWorkspace from './components/VerificationWorkspace.vue';

export type Workspace = 'calendar' | 'news' | 'ai' | 'verification';

declare module 'vue-router' {
  interface RouteMeta {
    workspace?: Workspace;
  }
}

export function createAppRouter(
  history: RouterHistory = createWebHashHistory(import.meta.env.BASE_URL),
) {
  return createRouter({
    history,
    routes: [
      { path: '/', redirect: { name: 'calendar' } },
      ...[
        { path: '/calendar', name: 'calendar' },
        { path: '/calendar/events/:id', name: 'calendar-event' },
        { path: '/calendar/fund', name: 'fund' },
        { path: '/calendar/fund/:id', name: 'fund-draft' },
        { path: '/calendar/new', name: 'calendar-new' },
        { path: '/calendar/reminders', name: 'reminders' },
      ].map((route) => ({
        ...route,
        component: CalendarWorkspace,
        meta: { workspace: 'calendar' as const },
      })),
      ...[
        { path: '/news', name: 'news' },
        { path: '/news/push', name: 'news-push' },
        { path: '/news/:id([1-9][0-9]*)', name: 'news-detail' },
      ].map((route) => ({
        ...route,
        component: NewsWorkspace,
        meta: { workspace: 'news' as const },
      })),
      { path: '/ai', name: 'ai', component: AISettings, meta: { workspace: 'ai' } },
      { path: '/verification', redirect: { name: 'verification', params: { tab: 'push' } } },
      {
        path: '/verification/:tab(push|sources|resources)',
        name: 'verification',
        component: VerificationWorkspace,
        meta: { workspace: 'verification' },
      },
      { path: '/not-found', name: 'not-found', component: NotFound },
      {
        path: '/:pathMatch(.*)*',
        // Notifications and saved links still use the original hash format.
        // Router redirects replace that entry, including on back/forward navigation.
        redirect: (to) => {
          const legacy = new URLSearchParams(to.path.slice(1));
          const event = legacy.get('event');
          const draft = legacy.get('draft');
          const news = legacy.get('news');
          if (event) return { name: 'calendar-event', params: { id: event } };
          if (draft) return { name: 'fund-draft', params: { id: draft } };
          if (news && /^[1-9][0-9]*$/.test(news))
            return { name: 'news-detail', params: { id: news } };
          if (news === '') return { name: 'news' };
          if (legacy.has('news-push')) return { name: 'news-push' };
          return { name: 'not-found' };
        },
      },
    ],
  });
}
