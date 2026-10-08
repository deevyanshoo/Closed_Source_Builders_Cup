import React, { lazy, Suspense } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { PlanningPage } from './page';
import { RecoveryPage } from './recovery-page';
import { VoicePage } from './voice-page';
import { ShipmentRecoveryPage } from './shipment-recovery-page';
import './style.css';

// Loaded on demand so the synthetic fixture data is not part of the main bundle.
const OffersPage = lazy(() => import('./offers-page').then(m => ({ default: m.OffersPage })));

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/planning" element={<PlanningPage />} />
        <Route path="/recovery" element={<RecoveryPage />} />
        <Route path="/shipment-recovery" element={<ShipmentRecoveryPage />} />
        <Route path="/offers" element={<Suspense fallback={<p className="notice">Loading offers…</p>}><OffersPage /></Suspense>} />
        <Route path="/scenarios" element={<VoicePage />} />
        <Route path="*" element={<Navigate to="/planning" replace />} />
      </Routes>
    </BrowserRouter>
  </React.StrictMode>,
);
