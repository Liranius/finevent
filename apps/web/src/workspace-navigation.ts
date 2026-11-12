import type { Router } from 'vue-router';

export interface NewsNavigation {
  lastSelected?: number;
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
  month: string;
  day: string;
  layout: 'month' | 'agenda';
  filter: string;
}

// Direct links have no preceding list entry. Replacing them avoids leaving the
// application or creating a detail -> list -> back-to-detail loop.
export function returnToList(router: Router, name: 'calendar' | 'news') {
  const target = { name };
  if (router.options.history.state.back === router.resolve(target).fullPath) router.back();
  else void router.replace(target);
}
