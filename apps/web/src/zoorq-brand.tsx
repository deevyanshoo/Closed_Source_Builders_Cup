import './zoorq-brand.css';

/** The open loop and its detour are the same route used by the landing sculpture. */
export function ZoorqMark({ className = '' }: { className?: string }) {
  return <svg className={`zoorq-mark ${className}`} viewBox="0 0 64 64" fill="none" aria-hidden="true" focusable="false">
    <path className="zoorq-mark-loop" d="M45.7 43.7A22 22 0 1 0 39.5 50.7" strokeWidth="8" strokeLinecap="round" />
    <path className="zoorq-mark-route" d="M39.5 50.7C47.5 47.5 49.5 42 45.7 43.7L59 58" strokeWidth="8" strokeLinecap="round" strokeLinejoin="round" />
    <path className="zoorq-mark-block" d="M47.2 47.6l3.2 3.2" strokeWidth="3" strokeLinecap="round" />
  </svg>;
}

export function ZoorqBrand({ compact = false }: { compact?: boolean }) {
  return <span className={`zoorq-brand-lockup${compact ? ' zoorq-brand-lockup-compact' : ''}`}>
    <ZoorqMark />
    <span className="zoorq-brand-type"><svg viewBox="0 0 174 32" role="img" aria-hidden="true" focusable="false">
      <g fill="none" stroke="currentColor" strokeWidth="5.5" strokeLinecap="square" strokeLinejoin="round">
        <path d="M3 4h25L3 28h25" />
        <rect x="38" y="4" width="25" height="24" rx="11" />
        <rect x="73" y="4" width="25" height="24" rx="11" />
        <path d="M109 28V4h17c7 0 11 3 11 9 0 6-4 9-11 9h-17m16-1 12 7" />
        <rect x="146" y="4" width="25" height="24" rx="11" />
        <path d="m163 22 9 8" />
      </g>
    </svg><span className="sr-only">ZOORQ</span></span>
  </span>;
}
