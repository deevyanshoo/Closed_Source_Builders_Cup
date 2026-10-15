import { Component, useEffect, useRef, useState, type CSSProperties, type ErrorInfo, type ReactNode } from 'react';
import { ThinkingOrb, type OrbState } from 'thinking-orbs';
import { useDemoTheme } from './demo-theme';
import './zoorq-lens.css';

export type ZoorqLensState = 'idle' | 'connecting' | 'listening' | 'thinking' | 'speaking' | 'muted' | 'error' | 'working' | 'searching' | 'solving' | 'composing';

const orbStates: Record<ZoorqLensState, OrbState> = {
  idle: 'working', muted: 'breathing', error: 'breathing', connecting: 'connecting',
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

type BoundaryProps = { children: ReactNode };
type BoundaryState = { failed: boolean };

class OrbFallbackBoundary extends Component<BoundaryProps, BoundaryState> {
  state: BoundaryState = { failed: false };

  static getDerivedStateFromError(): BoundaryState {
    return { failed: true };
  }

  componentDidCatch(_error: Error, _info: ErrorInfo): void {
    // The non-decorative fallback below keeps the voice panel layout stable.
  }

  render() {
    if (this.state.failed) return <span className="zoorq-lens-fallback" aria-hidden="true" />;
    return this.props.children;
  }
}

export function ZoorqLens({ state = 'idle', compact = false, className = '', level = 0 }: {
  state?: ZoorqLensState;
  compact?: boolean;
  className?: string;
  level?: number;
}) {
  const { resolvedTheme } = useDemoTheme();
  const root = useRef<HTMLDivElement>(null);
  const [visibleLevel, setVisibleLevel] = useState(() => clampOrbLevel(level));
  const latestLevel = useRef(clampOrbLevel(level));
  const lastLevelUpdate = useRef(0);
  const activeMotion = orbHasMotion(state);
  const safeLevel = clampOrbLevel(level);
  const orbTheme = resolvedTheme === 'dark' ? 'dark' : 'light';
  const color = resolvedTheme === 'dark' ? '#9bdcff' : '#1558ad';

  useEffect(() => {
    latestLevel.current = safeLevel;
    const now = performance.now();
    const delay = Math.max(0, 100 - (now - lastLevelUpdate.current));
    const timer = window.setTimeout(() => {
      lastLevelUpdate.current = performance.now();
      setVisibleLevel(latestLevel.current);
    }, delay);
    return () => window.clearTimeout(timer);
  }, [safeLevel]);

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
    <OrbFallbackBoundary>
      <ThinkingOrb
        className="zoorq-lens-canvas"
        state={orbStateFor(state)}
        size={64}
        theme={orbTheme}
        color={color}
        speed={state === 'speaking' ? 1.05 : 1}
        paused={!activeMotion}
        gravity={false}
        aria-hidden="true"
      />
    </OrbFallbackBoundary>
  </div>;
}
