import React, { lazy, Suspense } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import './style.css';
import { JudgesGuide } from './judges-guide';

// Loaded on demand so the synthetic fixture data is not part of the main bundle.
const PlanningPage = lazy(() => import('./page').then(m => ({ default: m.PlanningPage })));
const RecoveryPage = lazy(() => import('./recovery-page').then(m => ({ default: m.RecoveryPage })));
const VoicePage = lazy(() => import('./voice-page').then(m => ({ default: m.VoicePage })));
const ShipmentRecoveryPage = lazy(() => import('./shipment-recovery-page').then(m => ({ default: m.ShipmentRecoveryPage })));
const OffersPage = lazy(() => import('./offers-page').then(m => ({ default: m.OffersPage })));
const DispatchPage = lazy(() => import('./dispatch-page').then(m => ({ default: m.DispatchPage })));
const DemoPage = lazy(() => import('./demo-page').then(m => ({ default: m.DemoPage })));

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/planning" element={<Suspense fallback={<p className="notice">Loading planning…</p>}><PlanningPage /></Suspense>} />
        <Route path="/recovery" element={<Suspense fallback={<p className="notice">Loading recovery…</p>}><RecoveryPage /></Suspense>} />
        <Route path="/shipment-recovery" element={<Suspense fallback={<p className="notice">Loading shipment recovery…</p>}><ShipmentRecoveryPage /></Suspense>} />
        <Route path="/offers" element={<Suspense fallback={<p className="notice">Loading offers…</p>}><OffersPage /></Suspense>} />
        <Route path="/scenarios" element={<Suspense fallback={<p className="notice">Loading scenarios…</p>}><VoicePage /></Suspense>} />
        <Route path="/dispatch" element={<Suspense fallback={<p className="notice">Loading dispatch…</p>}><DispatchPage /></Suspense>} />
        <Route path="/demo" element={<><Suspense fallback={<p className="notice">Opening workspace…</p>}><DemoPage /></Suspense><JudgesGuide /></>} />
        <Route path="/" element={<Navigate to="/demo" replace />} />
        <Route path="*" element={<Navigate to="/demo" replace />} />
      </Routes>
    </BrowserRouter>
  </React.StrictMode>,
);
