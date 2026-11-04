export interface NewsNavigation {
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
  month: string;
  day: string;
  layout: 'month' | 'agenda';
  filter: string;
}
