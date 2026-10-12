// One header for all six workspace pages (#80): the same short links, the current page marked with aria-current,
// and each page's own synthetic and session status kept visible. Pages pass their status; nothing is inferred here.
import type { ReactNode } from 'react';
import { NavLink } from 'react-router-dom';
import type { SessionResponse } from '../../../contracts/generated/types';
import './workspace-nav.css';

export const WORKSPACE_PAGES: readonly (readonly [string, string])[] = [
  ['/planning', 'Planning'], ['/offers', 'Offers'], ['/dispatch', 'Dispatch'], ['/recovery', 'Recovery'],
  ['/shipment-recovery', 'Shipment recovery'], ['/scenarios', 'Scenarios'],
];

export function WorkspaceNav() {
  return <nav className="workspace-nav" aria-label="Workspace pages">
    {WORKSPACE_PAGES.map(([to, label]) => <NavLink key={to} className="recovery-back" to={to} end>{label}</NavLink>)}
  </nav>;
}

/** Header status for a page that reads the gateway session. A missing session is said, never assumed. */
export function sessionStatus(session: SessionResponse | null | undefined): string {
  if (!session) return 'Synthetic · session not confirmed';
  const mode = session.identity_mode === 'private_machine' ? 'private machine' : 'local development';
  return `Synthetic · ${mode} · ${session.role} · workspace ${session.workspace_id}`;
}

export function WorkspaceHeader({ section, status }: { section: string; status: ReactNode }) {
  return <header className="masthead workspace-masthead">
    <div className="brand">CLOSED <span>SOURCE</span><sup> / {section}</sup></div>
    <div className="masthead-meta"><span className="red-dot" aria-hidden="true">●</span> {status}</div>
    <WorkspaceNav />
  </header>;
}
