import { useEffect, useReducer, useRef, useState } from 'react';
import './judges-guide.css';

export const GUIDE_SESSION_KEY = 'zoorq-judge-guide-2026-10-18-v1';

type GuideStorage = Pick<Storage, 'getItem' | 'setItem'>;

export function claimFirstGuideVisit(storage: GuideStorage): boolean {
  try {
    if (storage.getItem(GUIDE_SESSION_KEY) === 'seen') return false;
    storage.setItem(GUIDE_SESSION_KEY, 'seen');
  } catch {
    // Private browsing or locked storage must not prevent the guide opening.
  }
  return true;
}

export function guideVisibility(_open: boolean, action: 'firstVisit' | 'reopen' | 'close'): boolean {
  return action !== 'close';
}

export function JudgesGuide() {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const [isOpen, dispatchVisibility] = useReducer(guideVisibility, false);
  const [fallback, setFallback] = useState(false);

  useEffect(() => {
    try {
      if (claimFirstGuideVisit(window.sessionStorage)) dispatchVisibility('firstVisit');
    } catch {
      dispatchVisibility('firstVisit');
    }
  }, []);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (isOpen && !dialog.open) {
      if (typeof dialog.showModal === 'function') {
        try {
          dialog.showModal();
        } catch {
          dialog.setAttribute('open', '');
          setFallback(true);
        }
      } else {
        dialog.setAttribute('open', '');
        setFallback(true);
      }
      dialog.querySelector<HTMLHeadingElement>('#judges-guide-title')?.focus({ preventScroll: true });
      dialog.scrollTop = 0;
    } else if (!isOpen && dialog.open) {
      if (typeof dialog.close === 'function' && !fallback) dialog.close();
      else dialog.removeAttribute('open');
    }
  }, [isOpen, fallback]);

  function closeGuide() {
    dispatchVisibility('close');
    triggerRef.current?.focus();
  }

  return (
    <div className="judges-guide-root">
      <button ref={triggerRef} className="judges-guide-trigger" type="button" onClick={() => dispatchVisibility('reopen')} aria-haspopup="dialog">
        Judge guide
      </button>
      {fallback && isOpen && <div className="judges-guide-fallback-backdrop" aria-hidden="true" />}
      <dialog
        ref={dialogRef}
        className="judges-guide-dialog"
        aria-labelledby="judges-guide-title"
        aria-describedby="judges-guide-tagline"
        onClose={() => { dispatchVisibility('close'); triggerRef.current?.focus(); }}
        onKeyDown={event => { if (fallback && event.key === 'Escape') closeGuide(); }}
      >
        <div className="judges-guide-content">
          <p className="judges-guide-kicker">A two-minute tour</p>
          <h1 id="judges-guide-title" tabIndex={-1}>Welcome to ZOORQ <span>— a product by ClosedSource</span></h1>
          <p id="judges-guide-tagline" className="judges-guide-tagline">See disruption. Close the stock gap.</p>

          <section aria-labelledby="judges-guide-start">
            <h2 id="judges-guide-start">Try it in 2 minutes — no signup</h2>
            <ol className="judges-guide-steps">
              <li>Open <strong>Solara Cables</strong> to start with a populated procurement workspace, or explore any of the 10 companies.</li>
              <li>Open <strong>News</strong> for company-relevant stories, sources, dates and business implications. Switch to <strong>Global Watch</strong> for wider trade, route and geopolitical signals. Public news refreshes hourly.</li>
              <li>Select <strong>Talk to ZOORQ</strong>. Speak naturally in your preferred language—the Gemini Live operator is multilingual and uses your current company’s workspace. Use <strong>Type to ZOORQ</strong> if your microphone is unavailable.</li>
            </ol>
          </section>

          <section className="judges-guide-prompts" aria-labelledby="judges-guide-ask">
            <h2 id="judges-guide-ask">Try asking</h2>
            <ul>
              <li>“What’s happening around the world that can affect my procurement?”</li>
              <li>“Which inventory risks need my attention, and why?”</li>
              <li>“Does our current plan prevent a stockout before the selected delivery?”</li>
              <li>“मेरी कंपनी की खरीद पर कौन-सी खबरें असर डाल सकती हैं?”</li>
              <li>“Explain that in Hindi.”</li>
            </ul>
          </section>

          <section aria-labelledby="judges-guide-further">
            <h2 id="judges-guide-further">Explore further</h2>
            <p>Inventory &amp; supply shows stock cover and routes; Procurement compares quotes; Drafts holds saved RFQs and emails. Drafts are unsent. Review proposed actions before applying them. External internet research asks for your consent.</p>
          </section>

          <p className="judges-guide-credits"><strong>Powered by Gemini on Vertex AI, Gemini Live, Cloud Run, Firestore and Google Maps.</strong></p>
          <div className="judges-guide-actions"><button className="judges-guide-primary" type="button" onClick={closeGuide}>Let’s explore</button></div>
        </div>
      </dialog>
    </div>
  );
}
