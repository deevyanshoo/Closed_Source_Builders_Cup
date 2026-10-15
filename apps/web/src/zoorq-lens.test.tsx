import { describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { clampOrbLevel, orbHasMotion, orbStateFor, ZoorqLens } from './zoorq-lens';

vi.mock('./demo-theme', () => ({ useDemoTheme: () => ({ preference: 'system', resolvedTheme: 'dark', setPreference: vi.fn() }) }));

describe('ZoorqLens', () => {
  it('maps voice activity to the established Thinking Orb animations', () => {
    expect(orbStateFor('listening')).toBe('listening');
    expect(orbStateFor('thinking')).toBe('solving');
    expect(orbStateFor('speaking')).toBe('composing');
    expect(orbStateFor('connecting')).toBe('connecting');
  });

  it('clamps invalid and out-of-range audio levels', () => {
    expect(clampOrbLevel(-0.2)).toBe(0);
    expect(clampOrbLevel(0.45)).toBe(0.45);
    expect(clampOrbLevel(2)).toBe(1);
    expect(clampOrbLevel(Number.NaN)).toBe(0);
  });

  it('keeps settled states static and active voice states animated', () => {
    expect(orbHasMotion('listening')).toBe(true);
    expect(orbHasMotion('thinking')).toBe(true);
    expect(orbHasMotion('speaking')).toBe(true);
    expect(orbHasMotion('connecting')).toBe(true);
    expect(orbHasMotion('idle')).toBe(false);
    expect(orbHasMotion('muted')).toBe(false);
    expect(orbHasMotion('error')).toBe(false);
  });

  it('renders the orb state and a clamped level without taking over audio state', () => {
    const markup = renderToStaticMarkup(<ZoorqLens state="listening" compact level={3} />);
    expect(markup).toContain('data-state="listening"');
    expect(markup).toContain('data-motion="active"');
    expect(markup).toContain('data-level="1.00"');
    expect(markup).toContain('aria-hidden="true"');
  });
});
