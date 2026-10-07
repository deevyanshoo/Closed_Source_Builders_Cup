import React from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { PlanningPage } from './page';
import { RecoveryPage } from './recovery-page';
import { VoicePage } from './voice-page';
import './style.css';

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/planning" element={<PlanningPage />} />
        <Route path="/recovery" element={<RecoveryPage />} />
        <Route path="/scenarios" element={<VoicePage />} />
        <Route path="*" element={<Navigate to="/planning" replace />} />
      </Routes>
    </BrowserRouter>
  </React.StrictMode>,
);
