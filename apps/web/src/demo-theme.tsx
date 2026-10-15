import { useEffect, useState } from 'react';

export type ThemePreference = 'light' | 'dark' | 'system';
export type ResolvedTheme = 'light' | 'dark';
export const THEME_STORAGE_KEY = 'zoorq-theme';
const themeEvent = 'zoorq-theme-change';

export function normalizeThemePreference(value: unknown): ThemePreference {
  return value === 'dark' || value === 'system' ? value : 'light';
}

export function resolveThemePreference(preference: ThemePreference, systemDark: boolean): ResolvedTheme {
  return preference === 'system' ? systemDark ? 'dark' : 'light' : preference;
}

function systemPrefersDark(): boolean {
  try { return window.matchMedia('(prefers-color-scheme: dark)').matches; } catch { return false; }
}

export function readDemoTheme(): { preference: ThemePreference; resolvedTheme: ResolvedTheme } {
  if (typeof document === 'undefined') return { preference: 'light', resolvedTheme: 'light' };
  const root = document.documentElement;
  const preference = normalizeThemePreference(root.getAttribute('data-zoorq-preference'));
  const theme = root.getAttribute('data-zoorq-theme');
  return { preference, resolvedTheme: theme === 'dark' || theme === 'light' ? theme : resolveThemePreference(preference, systemPrefersDark()) };
}

export function setDemoThemePreference(preference: ThemePreference) {
  const resolvedTheme = resolveThemePreference(preference, systemPrefersDark());
  const root = document.documentElement;
  root.setAttribute('data-zoorq-preference', preference);
  root.setAttribute('data-zoorq-theme', resolvedTheme);
  if (window.location.pathname === '/demo') {
    root.style.colorScheme = resolvedTheme;
    root.style.backgroundColor = resolvedTheme === 'dark' ? '#0c1220' : '#f7f9fc';
    const meta = document.querySelector<HTMLMetaElement>('meta[name="theme-color"]');
    if (meta) meta.content = resolvedTheme === 'dark' ? '#0c1220' : '#FFFFFF';
  }
  try { window.localStorage.setItem(THEME_STORAGE_KEY, preference); } catch { /* A blocked store must not block selection. */ }
  window.dispatchEvent(new CustomEvent(themeEvent, { detail: { preference, resolvedTheme } }));
}

export function useDemoTheme() {
  const [theme, setTheme] = useState(readDemoTheme);
  useEffect(() => {
    const update = () => setTheme(readDemoTheme());
    update();
    window.addEventListener(themeEvent, update);
    return () => window.removeEventListener(themeEvent, update);
  }, []);
  return { ...theme, setPreference: setDemoThemePreference };
}

export function ThemeControl() {
  const { preference, setPreference } = useDemoTheme();
  return <label className="zoorq-theme-control"><span>Appearance</span><select aria-label="Appearance" value={preference} onChange={event => setPreference(normalizeThemePreference(event.currentTarget.value))}><option value="light">Light</option><option value="dark">Dark</option><option value="system">System</option></select></label>;
}
