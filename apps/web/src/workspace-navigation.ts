import type { Router } from 'vue-router';

export interface NewsNavigation {
  lastSelected?: number;
  rowTop?: number;
  anchorTime?: number;
  pages?: number;
  filters: {
    q: string;
    category: string;
    industry: string;
    source: string;
    importance: string;
    order: string;
    range: string;
  };
  applied: string;
}
export interface CalendarNavigation {
  lastEvent?: string;
  rowTop?: number;
  month: string;
  day: string;
  layout: 'month' | 'agenda';
  filter: string;
}

export function linkedOrigin(
  router: Router,
  name: 'news-detail' | 'calendar-event' | 'news-notifications' | 'reminder-history',
) {
  const { back, linkedFrom } = router.options.history.state;
  return typeof back === 'string' && linkedFrom === back && router.resolve(back).name === name;
}

// Direct links have no preceding list entry. Replacing them avoids leaving the
// application or creating a detail -> list -> back-to-detail loop.
export function returnToList(router: Router, name: 'calendar' | 'news' | 'settings') {
  const target = { name };
  if (router.options.history.state.back === router.resolve(target).fullPath) router.back();
  else void router.replace(target);
}
