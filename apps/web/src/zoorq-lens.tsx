import { useEffect, useRef } from 'react';
import gsap from 'gsap';
import './zoorq-lens.css';

export type ZoorqLensState = 'idle' | 'connecting' | 'listening' | 'thinking' | 'speaking' | 'muted' | 'error' | 'working' | 'searching' | 'solving' | 'composing';

export function ZoorqLens({ state = 'idle', compact = false, className = '' }: { state?: ZoorqLensState; compact?: boolean; className?: string }) {
  const root = useRef<HTMLDivElement>(null);
  const activeMotion = state !== 'idle' && state !== 'muted' && state !== 'error';
  useEffect(() => {
    const element = root.current;
    if (!element || typeof window === 'undefined') return;
    const media = window.matchMedia('(prefers-reduced-motion: reduce)');
    let context: ReturnType<typeof gsap.context> | undefined;
    let entrance: ReturnType<typeof gsap.fromTo> | undefined;
    let spin: ReturnType<typeof gsap.to> | undefined;
    const updateVisibility = () => {
      if (document.hidden || media.matches) { entrance?.pause(); spin?.pause(); }
      else { entrance?.resume(); spin?.resume(); }
    };
    const start = () => {
      if (context || media.matches) return;
      context = gsap.context(() => {
        entrance = gsap.fromTo('.zoorq-lens-core', { scale: 0.92, opacity: 0.8 }, { scale: 1, opacity: 1, duration: 0.7, ease: 'power2.out' });
        if (activeMotion) spin = gsap.to('.zoorq-lens-rim', { rotation: 360, duration: state === 'thinking' || state === 'working' || state === 'searching' || state === 'solving' ? 9 : 22, repeat: -1, ease: 'none' });
      }, element);
      updateVisibility();
    };
    const updateMotion = () => { if (media.matches) { context?.revert(); context = undefined; entrance = undefined; spin = undefined; } else start(); };
    start();
    document.addEventListener('visibilitychange', updateVisibility);
    media.addEventListener('change', updateMotion);
    return () => { document.removeEventListener('visibilitychange', updateVisibility); media.removeEventListener('change', updateMotion); context?.revert(); };
  }, [state, activeMotion]);
  return <div ref={root} className={`zoorq-lens ${compact ? 'zoorq-lens-compact' : ''} ${className}`.trim()} data-state={state} data-motion={activeMotion ? 'active' : 'static'} aria-hidden="true"><div className="zoorq-lens-halo" /><div className="zoorq-lens-rim"><div className="zoorq-lens-metal" /><div className="zoorq-lens-inner"><div className="zoorq-lens-core"><div className="zoorq-lens-shine" /></div></div></div><div className="zoorq-lens-shadow" /></div>;
}
