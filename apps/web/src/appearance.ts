import { Dark } from 'quasar';

const key = 'finevent.appearance';
type Appearance = boolean | 'auto';

export function savedAppearance(): Appearance {
  try {
    const value = localStorage.getItem(key);
    if (value === 'dark' || value === 'light') return value === 'dark';
  } catch {
    // Storage can be unavailable in a restricted browser.
  }
  return 'auto';
}

export function setAppearance(mode: Appearance) {
  Dark.set(mode);
  try {
    if (mode === 'auto') localStorage.removeItem(key);
    else localStorage.setItem(key, mode ? 'dark' : 'light');
  } catch {
    // Quasar still applies the selection for this page.
  }
}
