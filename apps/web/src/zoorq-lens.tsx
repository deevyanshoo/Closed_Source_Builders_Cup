import { useEffect, useRef, useState, type CSSProperties } from 'react';
import { MODE_FRAMES, paintFrame, resolvePreset, type OrbState } from 'thinking-orbs/engine';
import { useDemoTheme } from './demo-theme';
import './zoorq-lens.css';

export type ZoorqLensState = 'idle' | 'connecting' | 'listening' | 'thinking' | 'speaking' | 'muted' | 'error' | 'working' | 'searching' | 'solving' | 'composing';

const orbStates: Record<ZoorqLensState, OrbState> = {
  idle: 'listening', muted: 'breathing', error: 'breathing', connecting: 'connecting',
  listening: 'listening', thinking: 'solving', speaking: 'composing', working: 'working',
  searching: 'searching', solving: 'solving', composing: 'composing',
};

export function orbStateFor(state: ZoorqLensState): OrbState {
  return orbStates[state];
}

export function clampOrbLevel(level: number): number {
  return Number.isFinite(level) ? Math.max(0, Math.min(1, level)) : 0;
}

export function orbHasMotion(state: ZoorqLensState): boolean {
  return state !== 'idle' && state !== 'muted' && state !== 'error';
}

export function orbCanvasBackingSize(cssSize: number, devicePixelRatio: number): { size: number; pixels: number; ratio: number } {
  const size = Number.isFinite(cssSize) ? Math.max(1, Math.round(cssSize)) : 1;
  const ratio = Number.isFinite(devicePixelRatio) ? Math.min(2, Math.max(1, devicePixelRatio)) : 1;
  return { size, pixels: Math.round(size * ratio), ratio };
}

export function orbFrameAt(state: ZoorqLensState, size: number, seconds: number) {
  const preset = resolvePreset(orbStateFor(state), 64);
  return MODE_FRAMES[preset.mode](size, seconds * preset.speed, preset.opts);
}

const STATIC_FRAME_SECONDS = 0.6;
const MAX_FRAME_MS = 1000 / 30;

export function ZoorqLens({ state = 'idle', compact = false, className = '', level = 0 }: {
  state?: ZoorqLensState;
  compact?: boolean;
  className?: string;
  level?: number;
}) {
  const { resolvedTheme } = useDemoTheme();
  const root = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [failed, setFailed] = useState(false);
  const [visibleLevel, setVisibleLevel] = useState(() => clampOrbLevel(level));
  const latestLevel = useRef(clampOrbLevel(level));
  const lastLevelUpdate = useRef(0);
  const activeMotion = orbHasMotion(state);
  const safeLevel = clampOrbLevel(level);

  useEffect(() => {
    latestLevel.current = safeLevel;
    const delay = Math.max(0, 100 - (performance.now() - lastLevelUpdate.current));
    const timer = window.setTimeout(() => {
      lastLevelUpdate.current = performance.now();
      setVisibleLevel(latestLevel.current);
    }, delay);
    return () => window.clearTimeout(timer);
  }, [safeLevel]);

  useEffect(() => {
    const surface = canvas.current;
    if (!surface) return;
    setFailed(false);
    let context: CanvasRenderingContext2D | null;
    try { context = surface.getContext('2d'); } catch { context = null; }
    if (!context) { setFailed(true); return; }

    const dark = resolvedTheme === 'dark';
    const tint = dark ? { r: 155, g: 220, b: 255 } : { r: 21, g: 88, b: 173 };
    const preset = resolvePreset(orbStateFor(state), 64);
    let phase = 0;
    let lastFrame = 0;
    let raf = 0;
    let inView = true;
    let reducedMotion = false;
    let failedDuringDraw = false;
    const media = window.matchMedia('(prefers-reduced-motion: reduce)');

    const resizeAndDraw = () => {
      if (failedDuringDraw) return;
      const size = Math.max(1, surface.clientWidth, surface.clientHeight);
      const backing = orbCanvasBackingSize(size, window.devicePixelRatio || 1);
      if (surface.width !== backing.pixels || surface.height !== backing.pixels) {
        surface.width = backing.pixels;
        surface.height = backing.pixels;
      }
      try {
        context!.setTransform(backing.ratio, 0, 0, backing.ratio, 0, 0);
        context!.clearRect(0, 0, backing.size, backing.size);
        const seconds = activeMotion && !reducedMotion ? phase : STATIC_FRAME_SECONDS;
        const frame = MODE_FRAMES[preset.mode](backing.size, seconds * preset.speed, preset.opts);
        paintFrame(context!, frame, dark, tint);
      } catch {
        failedDuringDraw = true;
        setFailed(true);
      }
    };

    const shouldAnimate = () => activeMotion && !reducedMotion && !document.hidden && inView && !failedDuringDraw;
    const stop = () => {
      if (raf) cancelAnimationFrame(raf);
      raf = 0;
      lastFrame = 0;
    };
    const animate = (timestamp: number) => {
      raf = 0;
      if (!shouldAnimate()) return;
      if (lastFrame && timestamp - lastFrame < MAX_FRAME_MS) {
        raf = requestAnimationFrame(animate);
        return;
      }
      const delta = lastFrame ? Math.min(0.05, (timestamp - lastFrame) / 1000) : 0;
      lastFrame = timestamp;
      phase += delta;
      resizeAndDraw();
      raf = requestAnimationFrame(animate);
    };
    const reconcile = () => {
      if (shouldAnimate()) {
        if (!raf) raf = requestAnimationFrame(animate);
      } else {
        stop();
        resizeAndDraw();
      }
    };
    const onMotionChange = () => { reducedMotion = media.matches; reconcile(); };
    const onVisibilityChange = () => reconcile();
    let resizeObserver: ResizeObserver | undefined;
    let intersectionObserver: IntersectionObserver | undefined;

    try {
      if (typeof ResizeObserver !== 'undefined') {
        resizeObserver = new ResizeObserver(resizeAndDraw);
        resizeObserver.observe(surface);
      }
      if (typeof IntersectionObserver !== 'undefined' && root.current) {
        intersectionObserver = new IntersectionObserver(entries => {
          inView = entries[0]?.isIntersecting ?? true;
          reconcile();
        });
        intersectionObserver.observe(root.current);
      }
      reducedMotion = media.matches;
      media.addEventListener('change', onMotionChange);
      document.addEventListener('visibilitychange', onVisibilityChange);
      resizeAndDraw();
      reconcile();
    } catch {
      failedDuringDraw = true;
      setFailed(true);
      stop();
    }

    return () => {
      stop();
      resizeObserver?.disconnect();
      intersectionObserver?.disconnect();
      media.removeEventListener('change', onMotionChange);
      document.removeEventListener('visibilitychange', onVisibilityChange);
    };
  }, [state, resolvedTheme, activeMotion]);

  return <div
    ref={root}
    className={`zoorq-lens ${compact ? 'zoorq-lens-compact' : ''} ${className}`.trim()}
    data-state={state}
    data-motion={activeMotion ? 'active' : 'static'}
    data-level={visibleLevel.toFixed(2)}
    aria-hidden="true"
    style={{
      '--orb-level': visibleLevel,
      '--orb-scale': 0.94 + visibleLevel * 0.12,
      '--orb-opacity': 0.86 + visibleLevel * 0.14,
    } as CSSProperties}
  >
    {failed ? <span className="zoorq-lens-fallback" /> : <canvas ref={canvas} className="zoorq-lens-canvas" />}
  </div>;
}
