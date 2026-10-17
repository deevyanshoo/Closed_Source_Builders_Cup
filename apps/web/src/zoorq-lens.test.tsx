import { describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { clampOrbLevel, orbCanvasBackingSize, orbFrameAt, orbHasMotion, orbStateFor, ZoorqLens } from './zoorq-lens';

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

  it('sizes canvas pixels from the displayed orb size and caps high DPI at two', () => {
    expect(orbCanvasBackingSize(180, 2)).toEqual({ size: 180, pixels: 360, ratio: 2 });
    expect(orbCanvasBackingSize(260, 3)).toEqual({ size: 260, pixels: 520, ratio: 2 });
    expect(orbCanvasBackingSize(Number.NaN, Number.NaN)).toEqual({ size: 1, pixels: 1, ratio: 1 });
  });

  it('uses package geometry at the rendered size for each voice animation', () => {
    for (const state of ['idle', 'connecting', 'listening', 'thinking', 'speaking', 'muted', 'error'] as const) {
      const frame = orbFrameAt(state, 240, 0.6);
      expect(frame.dots.length + frame.lines.length, state).toBeGreaterThan(0);
      expect([...frame.dots.map(dot => dot.x), ...frame.dots.map(dot => dot.y)].every(Number.isFinite), state).toBe(true);
    }
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
