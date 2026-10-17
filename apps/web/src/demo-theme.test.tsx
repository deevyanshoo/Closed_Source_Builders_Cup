import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import { ThemeControl, normalizeThemePreference, readDemoTheme, resolveThemePreference, setDemoThemePreference } from './demo-theme';

describe('ZOORQ appearance', () => {
  it('accepts only saved preferences and keeps light as the default', () => {
    expect(normalizeThemePreference('dark')).toBe('dark');
    expect(normalizeThemePreference('system')).toBe('system');
    expect(normalizeThemePreference('unexpected')).toBe('light');
    expect(normalizeThemePreference(null)).toBe('light');
    expect(resolveThemePreference('system', true)).toBe('dark');
    expect(resolveThemePreference('system', false)).toBe('light');
    expect(resolveThemePreference('light', true)).toBe('light');
  });

  it('renders an accessible selector on the server', () => {
    const html = renderToStaticMarkup(<ThemeControl />);
    expect(html).toContain('Appearance');
    expect(html).toContain('Light');
    expect(html).toContain('Dark');
    expect(html).toContain('System');
  });

  it('persists an explicit selection and exposes the same result to other consumers', () => {
    const attributes: Record<string, string> = {};
    const values: Record<string, string> = {};
    const dispatched: string[] = [];
    const root = { style: { colorScheme: '', backgroundColor: '' }, setAttribute: (key: string, value: string) => { attributes[key] = value; }, getAttribute: (key: string) => attributes[key] };
    const meta = { content: '#FFFFFF' };
    vi.stubGlobal('document', { documentElement: root, querySelector: () => meta });
    vi.stubGlobal('window', { location: { pathname: '/demo' }, matchMedia: () => ({ matches: true }), localStorage: { setItem: (key: string, value: string) => { values[key] = value; } }, dispatchEvent: (event: { type: string }) => { dispatched.push(event.type); } });
    vi.stubGlobal('CustomEvent', class { type: string; constructor(type: string) { this.type = type; } });
    try {
      setDemoThemePreference('light');
      expect(values['zoorq-theme']).toBe('light');
      expect(readDemoTheme()).toEqual({ preference: 'light', resolvedTheme: 'light' });
      setDemoThemePreference('system');
      expect(readDemoTheme()).toEqual({ preference: 'system', resolvedTheme: 'dark' });
      expect(meta.content).toBe('#0c1220');
      expect(dispatched).toEqual(['zoorq-theme-change', 'zoorq-theme-change']);
    } finally { vi.unstubAllGlobals(); }
  });

  it('sets the initial dark theme before React even if storage is unavailable', () => {
    const source = readFileSync(new URL('../public/assets/theme-init.js', import.meta.url), 'utf8');
    const attributes: Record<string, string> = {};
    const meta = { content: '#FFFFFF' };
    const listeners: Record<string, (event: { key?: string; newValue?: string | null }) => void> = {};
    const mediaListeners: Array<() => void> = [];
    const media = { matches: true, addEventListener: (_: string, listener: () => void) => mediaListeners.push(listener) };
    let stored: string | null = 'system';
    const window = {
      location: { pathname: '/demo' },
      localStorage: { getItem: () => stored, setItem: (_: string, value: string) => { stored = value; } },
      matchMedia: () => media,
      addEventListener: (name: string, listener: (event: { key?: string; newValue?: string | null }) => void) => { listeners[name] = listener; },
      dispatchEvent: () => true,
    };
    const document = { documentElement: { style: { colorScheme: '', backgroundColor: '' }, setAttribute: (name: string, value: string) => { attributes[name] = value; }, getAttribute: (name: string) => attributes[name] }, querySelector: () => meta };
    runInNewContext(source, { window, document, CustomEvent: class { constructor(_: string, __: unknown) {} } });
    expect(attributes['data-zoorq-theme']).toBe('dark');
    expect(attributes['data-zoorq-preference']).toBe('system');
    expect(meta.content).not.toBe('#FFFFFF');
    media.matches = false;
    mediaListeners[0]();
    expect(attributes['data-zoorq-theme']).toBe('light');
    listeners.storage({ key: 'zoorq-theme', newValue: 'dark' });
    expect(attributes['data-zoorq-theme']).toBe('dark');
    stored = null;
    listeners.storage({ key: 'zoorq-theme', newValue: null });
    expect(attributes['data-zoorq-theme']).toBe('light');

    const blockedWindow = { ...window, localStorage: { getItem: () => { throw new Error('blocked'); } } };
    expect(() => runInNewContext(source, { window: blockedWindow, document, CustomEvent: class { constructor(_: string, __: unknown) {} } })).not.toThrow();
    expect(attributes['data-zoorq-theme']).toBe('light');
  });
});
