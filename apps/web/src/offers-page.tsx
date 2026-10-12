import { useEffect, useMemo, useRef, useState } from 'react';
import type { ChangeEvent, FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { createFixtureAdapter, dataAge, fieldEntries, fixtureInfo, formatValue, offerReport, OffersError } from './offers';
import { ReportDownloads } from './report-downloads';
import type { BlockingReason, Field, Offer } from './offers';
import { bindOfferExtraction, createQuoteService, loadQuoteSession, QuoteServiceError } from './offers-service';
import type { QuoteSession, ServiceField, ServiceOffer, ServiceOfferSummary, Sourcing } from './offers-service';
import { EVENTS } from './offers-scenario';
import type { EventName, ScenarioInputs, ScenarioResult, Validity } from './offers-scenario';
import { getQuoteShipmentBridge, handoffUrl, stageHandoff, validBridgeCandidate } from './quote-shipment-bridge';
import type { BridgeCandidate } from './quote-shipment-bridge';
import './offers.css';
import { WorkspaceHeader } from './workspace-nav';

type View = { kind: 'empty' } | { kind: 'loading'; label: string } | { kind: 'error'; message: string } | { kind: 'offer' };
const statusLabel = (s: string) => s.replaceAll('_', ' ');
const fieldLabel = (name: string) => name.replace(/^items\[(\d+)\]\./, (_, i) => `Item ${Number(i) + 1} · `).replaceAll('_', ' ');
const UNITS = ['tonne', 'kg', 'lb'];
const INCOTERMS = ['EXW', 'FCA', 'FAS', 'FOB', 'CFR', 'CIF', 'CPT', 'CIP', 'DAP', 'DPU', 'DDP'];
const PRICE_TERMS = [
  ['fx_basis', 'FX basis'], ['fx_terms', 'FX terms'], ['fx_conversion', 'FX conversion'],
  ['quotational_period', 'Quotational period'],
] as const;

function Quote({ text, quote }: { text: string; quote: string }) {
  const at = quote ? text.toLowerCase().indexOf(quote.toLowerCase()) : -1;
  if (at < 0) return <>{text}</>;
  return <>{text.slice(0, at)}<mark>{text.slice(at, at + quote.length)}</mark>{text.slice(at + quote.length)}</>;
}



export function Editor({ field, onSubmit, onCancel, busy, service = false }: { field: Pick<Field, 'name' | 'kind' | 'value'>; onSubmit: (value: unknown) => void; onCancel: () => void; busy: boolean; service?: boolean }) {
  const v = (field.value ?? {}) as Record<string, any>;
  const [text, setText] = useState(typeof field.value === 'string' || typeof field.value === 'number' ? String(field.value) : '');
  const [amount, setAmount] = useState<string>(v.amount ?? v.unit_price?.amount ?? v.premium?.amount ?? '');
  const [unit, setUnit] = useState<string>(v.unit ?? v.per_unit ?? 'tonne');
  const [period, setPeriod] = useState<string>(v.period ?? '');
  const [currency, setCurrency] = useState<string>(v.unit_price?.currency ?? v.premium?.currency ?? '');
  const [priceType, setPriceType] = useState<'fixed' | 'index_linked'>(v.type === 'index_linked' ? 'index_linked' : 'fixed');
  const [index, setIndex] = useState<string>(v.index ?? '');
  const [averaging, setAveraging] = useState<string>(v.averaging_period ?? '');
  const [priceTerms, setPriceTerms] = useState<Record<string, string>>(
    Object.fromEntries(PRICE_TERMS.map(([name]) => [name, typeof v[name] === 'string' ? v[name] : ''])));
  const [code, setCode] = useState<string>(v.code ?? 'FOB');
  const [place, setPlace] = useState<string>(v.place ?? '');
  const indexLinked = field.kind === 'pricing' && priceType === 'index_linked';

  function submit(event: FormEvent) {
    event.preventDefault();
    const trimmed = text.trim();
    switch (field.kind) {
      case 'integer': return onSubmit(/^\d+$/.test(trimmed) ? Number(trimmed) : trimmed);
      case 'quantity': return onSubmit({ amount: amount.trim(), unit, ...(field.name.endsWith('capacity') && period ? { period } : {}) });
      case 'incoterm': return onSubmit({ code, place: place.trim() || null });
      case 'pricing': return onSubmit(indexLinked
        ? { type: 'index_linked', index: index.trim(), averaging_period: averaging.trim(),
          premium: { amount: amount.trim(), currency: currency.trim() }, per_unit: unit,
          ...Object.fromEntries(PRICE_TERMS.filter(([name]) => priceTerms[name].trim()).map(([name]) => [name, priceTerms[name].trim()])) }
        : { type: 'fixed', unit_price: { amount: amount.trim(), currency: currency.trim() }, per_unit: unit });
      default: return onSubmit(trimmed);
    }
  }

  const unitSelect = <label className="field"><span className="field-label">Unit</span><select value={unit} onChange={e => setUnit(e.target.value)}>{UNITS.map(u => <option key={u}>{u}</option>)}</select></label>;
  let inputs;
  if (field.kind === 'quantity') inputs = <><label className="field"><span className="field-label">Amount</span><input inputMode="decimal" value={amount} onChange={e => setAmount(e.target.value)} /></label>{unitSelect}
    {field.name.endsWith('capacity') && <label className="field"><span className="field-label">Per</span><select value={period} onChange={e => setPeriod(e.target.value)}><option value="">(none)</option>{['week', 'month', 'quarter', 'year'].map(p => <option key={p}>{p}</option>)}</select></label>}</>;
  else if (field.kind === 'pricing') inputs = <><label className="field"><span className="field-label">Price type</span><select value={priceType} onChange={e => setPriceType(e.target.value as 'fixed' | 'index_linked')}><option value="fixed">Fixed</option><option value="index_linked">Index-linked</option></select></label>
    {indexLinked && <><label className="field offers-wide"><span className="field-label">Index</span><input value={index} onChange={e => setIndex(e.target.value)} /></label>
      <label className="field offers-wide"><span className="field-label">Averaging period</span><input value={averaging} onChange={e => setAveraging(e.target.value)} /></label></>}
    <label className="field"><span className="field-label">{indexLinked ? 'Premium' : 'Price'}</span><input inputMode="decimal" value={amount} onChange={e => setAmount(e.target.value)} /></label>
    <label className="field"><span className="field-label">Currency</span><input value={currency} maxLength={3} onChange={e => setCurrency(e.target.value.toUpperCase())} /></label>{unitSelect}
    {indexLinked && PRICE_TERMS.map(([name, label]) => <label className="field offers-wide" key={name}><span className="field-label">{label}</span><input value={priceTerms[name]} onChange={e => setPriceTerms({ ...priceTerms, [name]: e.target.value })} /></label>)}</>;
  else if (field.kind === 'incoterm') inputs = <><label className="field"><span className="field-label">Code</span><select value={code} onChange={e => setCode(e.target.value)}>{INCOTERMS.map(c => <option key={c}>{c}</option>)}</select></label>
    <label className="field offers-wide"><span className="field-label">Named place</span><input value={place} onChange={e => setPlace(e.target.value)} /></label></>;
  else if (field.kind === 'material') inputs = service
    ? <label className="field"><span className="field-label">Material</span><input value={text} onChange={e => setText(e.target.value)} /></label>
    : <label className="field"><span className="field-label">Material</span><select value={text} onChange={e => setText(e.target.value)}><option value="">Choose</option>{fixtureInfo.materials.map(m => <option key={m}>{m}</option>)}</select></label>;
  else inputs = <label className="field offers-wide"><span className="field-label">{field.kind === 'date' ? 'Date (YYYY-MM-DD)' : field.kind === 'currency' ? 'ISO currency' : 'Value'}</span><input value={text} onChange={e => setText(e.target.value)} /></label>;

  return <form className="offers-editor" onSubmit={submit} aria-label={`Correct ${fieldLabel(field.name)}`}>
    <div className="offers-editor-grid">{inputs}</div>
    <p className="field-hint">Amounts stay exactly as typed (decimal text). Your value is recorded as a reviewer entry. Economic corrections need service revalidation before save.</p>
    <div className="offers-editor-actions"><button className="secondary-button" type="submit" disabled={busy}>Apply correction</button><button className="text-button" type="button" onClick={onCancel}>Cancel</button></div>
  </form>;
}

function Reasons({ reasons, title }: { reasons: BlockingReason[]; title: string }) {
  return <div className="notice notice-error offers-reasons" role="alert"><strong>{title}</strong><ul>{reasons.map(r => <li key={`${r.code}:${r.field}`}>{fieldLabel(r.field)}: {statusLabel(r.code.replace('field_', ''))}</li>)}</ul></div>;
}

export function FieldName({ name, field, selected, onSelect }: { name: string; field: Pick<Field, 'critical'>; selected: boolean; onSelect: (name: string) => void }) {
  return <button type="button" className="offers-field-button" aria-pressed={selected} onClick={event => { event.stopPropagation(); onSelect(name); }}>
    <span className="sr-only">Show source for </span>{fieldLabel(name)}{field.critical && <small>critical</small>}
  </button>;
}

export function EditButton({ name, onEdit }: { name: string; onEdit: (name: string) => void }) {
  return <button className="text-button" type="button" aria-label={`Edit ${fieldLabel(name)}`} onClick={event => { event.stopPropagation(); onEdit(name); }}>Edit</button>;
}

export function FieldStatus({ field }: { field: Pick<Field, 'status' | 'source'> }) {
  return <><span className="offers-chip" data-status={field.status}><span className="sr-only">Field status: </span>{field.status}</span>
    {field.source === 'user' && <small>reviewer entry</small>}</>;
}

export type ScenarioState = { kind: 'empty' } | { kind: 'loading' } | { kind: 'error'; message: string } | { kind: 'result'; result: ScenarioResult };
const EVENT_LABELS: Record<EventName, string> = {
  shipment: 'Shipment date', bill_of_lading: 'Bill of lading date', documents: 'Documents presented', invoice: 'Invoice date',
  delivery: 'Delivery date', arrival: 'Arrival date',
};
const words = (s: string) => s.replaceAll('_', ' ');
const moneyLabel = (m: { amount: string | null; currency: string | null } | null) => (m?.amount ? `${m.amount} ${m.currency}` : null);

export function ScenarioForm({ offer, offers, defaultDate, busy, onRun }: { offer: Offer; offers: Offer[]; defaultDate: string; busy: boolean; onRun: (inputs: ScenarioInputs) => void }) {
  const [decision, setDecision] = useState(defaultDate);
  const [dates, setDates] = useState<Record<EventName, string>>(Object.fromEntries(EVENTS.map(e => [e, ''])) as Record<EventName, string>);
  const [alternative, setAlternative] = useState('');
  const [rate, setRate] = useState('');
  function submit(event: FormEvent) {
    event.preventDefault();
    onRun({ decision_date: decision.trim(), dates: Object.fromEntries(EVENTS.map(e => [e, dates[e].trim() || null])),
      rate_percent: rate.trim() || null, alternative_offer_id: alternative || null });
  }
  return <form className="offers-scenario-form" onSubmit={submit} aria-label="Decision date and payment timing scenario">
    <div className="offers-scenario-grid">
      <label className="field"><span className="field-label">Decision date</span><input type="date" required value={decision} onChange={e => setDecision(e.target.value)} /></label>
      {EVENTS.map(e => <label className="field" key={e}><span className="field-label">{EVENT_LABELS[e]}</span>
        <input type="date" value={dates[e]} onChange={ev => setDates({ ...dates, [e]: ev.target.value })} /></label>)}
      <label className="field offers-wide"><span className="field-label">Compare with the payment terms of</span>
        <select value={alternative} onChange={e => setAlternative(e.target.value)}>
          <option value="">No comparison</option>
          {offers.filter(o => o.offer_id !== offer.offer_id).map(o => <option key={o.offer_id} value={o.offer_id}>{o.fixture_id} · {formatValue('text', o.fields.payment_terms.value)}</option>)}
        </select></label>
      <label className="field"><span className="field-label">Annual rate (%, optional)</span><input inputMode="decimal" value={rate} onChange={e => setRate(e.target.value)} /></label>
    </div>
    <p className="field-hint">Event dates are your assumptions; leave a date empty if it is unknown. The order is placed on the decision date. No rate is assumed: without one, no cost of timing is shown.</p>
    <button className="secondary-button" type="submit" disabled={busy}>Run scenario</button>
  </form>;
}

function validityText(v: Validity) {
  switch (v.state) {
    case 'valid': return `Open: valid until ${v.valid_until}, ${v.days_left} ${v.days_left === 1 ? 'day' : 'days'} left.`;
    case 'expired': return `Expired: valid until ${v.valid_until}. The offer stays listed but is excluded from comparison.`;
    case 'not_stated': return 'Validity not stated in the document. It is not treated as open, so the offer is excluded from comparison.';
    default: return 'Validity is unresolved (conflict or unverified), so the offer is excluded from comparison.';
  }
}

export function ScenarioView({ state, offers }: { state: ScenarioState; offers: Offer[] }) {
  const label = (id: string) => offers.find(o => o.offer_id === id)?.fixture_id ?? id;
  if (state.kind === 'empty') return <div className="empty-state offers-scenario-empty"><h3>No scenario yet</h3><p>Enter a decision date and any known event dates, then run the scenario. Nothing is saved or sent.</p></div>;
  if (state.kind === 'loading') return <div className="empty-state" aria-busy="true"><h3>Working out the scenario</h3><p>Checking validity and placing each payment on its event date.</p></div>;
  if (state.kind === 'error') return <div className="notice notice-error" role="alert"><strong>Could not run the scenario</strong><p>{state.message}</p></div>;
  const r = state.result, terms = r.payment_terms, c = r.terms_comparison;
  const change = c?.weighted_days_change ? Number(c.weighted_days_change) : 0;
  return <div className="offers-scenario-result" role="region" aria-label="Scenario result">
    <h4>On the decision date, {r.decision_date}</h4>
    <p>{validityText(r.validity.decision)}</p>
    <p className="field-hint">On {r.as_of}: {validityText(r.validity.as_of)}</p>
    <p>In comparison: {r.comparison.as_of.included.length} quotes on {r.as_of}, {r.comparison.decision.included.length} on {r.decision_date}.</p>
    {r.comparison.newly_expired.length > 0 && <><p><strong>Leaves the comparison after the decision date moves:</strong></p>
      <ul>{r.comparison.newly_expired.map(e => <li key={e.offer_id}>{label(e.offer_id)}, valid until {e.valid_until}</li>)}</ul></>}
    <details><summary>Excluded on {r.decision_date} ({r.comparison.decision.excluded.length})</summary>
      <ul>{r.comparison.decision.excluded.map(e => <li key={e.offer_id}>{label(e.offer_id)}: {words(e.reason)}{e.valid_until ? ` (valid until ${e.valid_until})` : ''}</li>)}</ul></details>

    <h4>Payment terms <span className="offers-chip" data-status={terms.status === 'parsed' ? 'extracted' : 'unverified'}><span className="sr-only">Payment terms status: </span>{words(terms.status)}</span></h4>
    {terms.text ? <p className="offers-evidence">{terms.evidence.map(e => <code key={e.locator}>{e.locator}</code>)}{terms.source === 'user' && <small>reviewer entry</small>} <q>{terms.text}</q></p>
      : <p className="inline-empty">{terms.reasons.includes('revalidation_required') ? 'Payment terms were corrected in this tab and need service revalidation before they can be read.' : 'Not stated in the document.'}</p>}
    {terms.unrecognised.length > 0 && <p className="offers-reason">Not understood, so given no share or date: {terms.unrecognised.map(t => <q key={t}>{t}</q>)}</p>}
    <p>{r.amount.amount ? `Order value: ${r.amount.amount} ${r.amount.currency} (quantity times unit price, quote currency).` : `Order value not available: ${words(r.amount.reason ?? 'unknown')}. Shares are shown as percentages only.`}</p>
    {r.schedule.lines.length > 0 && <div className="table-scroll"><table className="offers-table offers-schedule">
      <caption>Payment schedule</caption>
      <thead><tr><th>Share</th><th>Instrument</th><th>When</th><th>Date</th><th>Amount</th></tr></thead>
      <tbody>{r.schedule.lines.map((l, i) => <tr key={i}>
        <td>{l.percent ? `${l.percent}%` : 'Not stated'}{l.percent_basis === 'single_term' && <small>single term, whole price</small>}</td>
        <td>{words(l.instrument)}</td>
        <td>{l.anchor === 'not_stated' ? 'event not stated' : l.timing === 'on_or_before'
          ? (l.days ? `by ${l.days} days before ${words(l.anchor)}` : `on or before ${words(l.anchor)}`)
          : `${words(l.timing)} ${words(l.anchor)} + ${l.days ?? 0} days`}</td>
        <td>{l.due_date ? <>{l.due_date}<small>day {l.days_from_decision}</small></> : <span className="offers-reason">{words(l.reason ?? 'unknown').replace(':', ': ')}</span>}</td>
        <td>{moneyLabel(l.amount) ?? 'Not available'}</td>
      </tr>)}</tbody>
    </table></div>}
    <p>{r.schedule.complete ? `Amount-weighted payment day: ${r.schedule.weighted_days} days after the decision date.` : `Schedule incomplete: ${r.schedule.missing.map(m => words(m).replace(':', ': ')).join(', ')}.`}</p>

    {c && r.alternative && <div className="offers-scenario-compare">
      <h4>Compared with the payment terms of {label(r.alternative.offer_id)}</h4>
      <p><q>{r.alternative.payment_terms.text ?? 'not stated'}</q>, placed on the same order value and event dates.</p>
      {c.reason === 'schedule_incomplete' ? <p>Not compared: one of the schedules is incomplete.</p> : <>
        <p>Payment moves {Math.abs(change)} days {change > 0 ? 'later' : change < 0 ? 'earlier' : '(no change)'} on average.</p>
        {c.value_of_later_payment ? <p>At {c.rate_percent}% a year, simple interest over {c.day_count} days, that timing is worth <strong>{c.value_of_later_payment.amount} {c.value_of_later_payment.currency}</strong> to the buyer (negative means it costs the buyer). Rounded half-even to 0.01.</p>
          : <p>{c.reason === 'no_rate_entered' ? 'Enter an annual rate to value this. No rate is assumed.' : `Not valued: ${words(c.reason ?? '')}.`}</p>}
      </>}
    </div>}
    <details open><summary>Assumptions</summary><ul>{r.assumptions.map(a => <li key={a}>{a}</li>)}</ul></details>
  </div>;
}

function FixtureOffersPage() {
  const adapter = useMemo(() => createFixtureAdapter(), []);
  const [view, setView] = useState<View>({ kind: 'empty' });
  const [offer, setOffer] = useState<Offer | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ text: string; reasons?: BlockingReason[]; stale?: boolean } | null>(null);
  const serial = useRef(0);
  const age = dataAge(fixtureInfo.asOf, new Date());
  const samples = useMemo(() => adapter.list(), [adapter]);
  const [scenario, setScenario] = useState<ScenarioState>({ kind: 'empty' });
  const scenarioSerial = useRef(0);

  function show(next: Offer) {
    scenarioSerial.current += 1;
    setScenario({ kind: 'empty' });
    setOffer(next);
    setView({ kind: 'offer' });
  }

  async function onFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    const mine = ++serial.current;
    setView({ kind: 'loading', label: `Reading ${file.name}` });
    setOffer(null); setEditing(null); setSelected(null); setMessage(null);
    try {
      const next = await adapter.upload(file);
      if (mine === serial.current) show(next);
    } catch (error) {
      if (mine === serial.current) setView({ kind: 'error', message: error instanceof Error ? error.message : 'The file could not be read.' });
    }
  }

  function openSample(id: string) {
    serial.current += 1;
    setEditing(null); setSelected(null); setMessage(null);
    if (id) show(adapter.get(id));
  }

  async function run(action: () => Promise<Offer>, done: string) {
    if (!offer) return;
    setBusy(true);
    setMessage(null);
    try {
      show(await action());
      setEditing(null);
      setMessage({ text: done });
    } catch (error) {
      const err = error instanceof OffersError ? error : null;
      // A refused change (blocked save, invalid value) leaves the shown offer current; other failures may not.
      const current = err && ['save_blocked', 'invalid_value', 'invalid_transition'].includes(err.kind);
      if (current && err.kind === 'save_blocked') show(adapter.get(offer.offer_id));
      setMessage({ text: error instanceof Error ? error.message : 'The change failed.', reasons: err?.reasons, stale: !current });
    } finally {
      setBusy(false);
    }
  }

  async function runScenario(inputs: ScenarioInputs) {
    if (!offer) return;
    const mine = ++scenarioSerial.current;
    setScenario({ kind: 'loading' });
    try {
      const result = await adapter.scenario(offer.offer_id, inputs);
      if (mine === scenarioSerial.current) setScenario({ kind: 'result', result });
    } catch (error) {
      if (mine === scenarioSerial.current) setScenario({ kind: 'error', message: error instanceof Error ? error.message : 'The scenario could not be computed.' });
    }
  }

  const evidenceLocators = new Set(selected && offer ? fieldEntries(offer).find(([n]) => n === selected)?.[1].evidence.map(e => e.locator) : []);

  return <main className="shell offers-shell">
    <WorkspaceHeader section="OFFERS" status="Synthetic · local adapter" />

    <section className="recovery-intro">
      <p className="eyebrow">Quote intelligence · issue 3</p>
      <h1>Supplier quotes, checked against their source</h1>
      <p>Each value is shown next to the exact page line or cell it came from. Missing, conflicting or unverifiable values stay empty until a reviewer resolves them.</p>
    </section>

    <div className="offers-adapter" role="note">
      <strong>SYNTHETIC FIXTURE DATA · LOCAL ADAPTER · NOT CONNECTED TO THE GATEWAY</strong>
      <span>{fixtureInfo.notice} Extracted with the rules pipeline at revision {fixtureInfo.revision}, as of {fixtureInfo.asOf}. Eligibility is previewed against the current UTC date when you act. Corrections and saves stay in this browser tab.</span>
      {age.old && <span className="offers-old">Old data: these fixtures are {age.days} days old. Re-export them before relying on what you see.</span>}
    </div>

    <section className="offers-load">
      <label className="field"><span className="field-label">Upload a quote (PDF, XLSX or CSV)</span><input type="file" accept=".pdf,.xlsx,.csv" onChange={onFile} /></label>
      <label className="field"><span className="field-label">Or open a synthetic sample</span>
        <select value={offer?.offer_id ?? ''} onChange={e => openSample(e.target.value)}>
          <option value="">Choose a sample quote</option>
          {samples.map(s => <option key={s.offer_id} value={s.offer_id}>{s.fixture_id} · {s.document.filename} ({s.split})</option>)}
        </select></label>
    </section>

    {view.kind === 'empty' && <div className="empty-state"><div className="empty-mark">∅</div><h3>No quote loaded</h3><p>Upload one of the synthetic fixture files or pick a sample to review its extracted offer.</p></div>}
    {view.kind === 'loading' && <div className="empty-state" aria-busy="true"><h3>{view.label}</h3><p>Matching the file against the synthetic fixtures.</p></div>}
    {view.kind === 'error' && <div className="notice notice-error" role="alert"><strong>Could not load this quote</strong><p>{view.message}</p></div>}

    {view.kind === 'offer' && offer && <section className="offers-workspace">
      <div className="offers-head">
        <div>
          <p className="eyebrow">{offer.document.filename} · {offer.document.media_type} · {offer.extractor} extractor</p>
          <h2>{formatValue('text', offer.fields.supplier_name.value)}</h2>
          <p className="revision-line">Document {offer.document.document_id.slice(0, 23)}… · version {offer.version}</p>
        </div>
        <div className="offers-status" data-status={offer.status}>{statusLabel(offer.status)}</div>
      </div>

      {offer.blocking_reasons.length > 0 && <Reasons reasons={offer.blocking_reasons} title="Save is blocked until these are resolved" />}
      {offer.revalidation_required?.length ? <div className="notice notice-error" role="note">Derived quantity, price, currency or validity values affected by a correction are unavailable. This local adapter cannot recompute Python normalization or FX. Authoritative service revalidation is required before save.</div> : null}
      {message && <div className={`notice ${message.reasons?.length || message.stale ? 'notice-error' : ''}`} role="status">
        <strong>{message.text}</strong>
        {message.reasons && message.reasons.length > 0 && <p>See the blocking reasons above.</p>}
        {message.stale && <p>Showing the last version that loaded successfully (version {offer.version}).</p>}
      </div>}
      {offer.review_flags.length > 0 && <div className="offers-flags" aria-label="Review flags">
        <h3>Review flags</h3>
        <ul>{offer.review_flags.map((f, i) => <li key={`${f.flag}:${f.locator}:${i}`}><span className="evidence-tag">{statusLabel(f.flag)}</span> {f.locator && <code>{f.locator}</code>} <q>{String(f.text)}</q>{f.flag === 'embedded_instruction' && <em> Shown as document text only. It was not followed.</em>}</li>)}</ul>
      </div>}

      <div className="offers-grid">
        <div className="table-scroll"><table className="offers-table">
          <caption>Extracted fields and their source</caption>
          <thead><tr><th>Field</th><th>Value</th><th>Status</th><th>Source text</th><th></th></tr></thead>
          <tbody>{fieldEntries(offer).map(([name, field]) => <tr key={name} className={selected === name ? 'offers-selected' : ''} onClick={() => setSelected(name)}>
            <th scope="row"><FieldName name={name} field={field} selected={selected === name} onSelect={setSelected} /></th>
            <td>{editing === name
              ? <Editor field={field} busy={busy} onCancel={() => setEditing(null)} onSubmit={value => run(() => adapter.correct(offer.offer_id, name, value, offer.version), `Corrected ${fieldLabel(name)}.`)} />
              : formatValue(field.kind, field.value)}
              {field.status === 'conflict' && <ul className="offers-candidates">{field.candidates.filter(c => c.verified).map((c, i) => <li key={i}>{formatValue(field.kind, c.value)} <code>{c.locators.join(', ')}</code></li>)}</ul>}
              {field.status === 'unverified' && field.reasons.length > 0 && <small className="offers-reason">{field.reasons.slice(0, 3).join(', ').replaceAll('_', ' ')}</small>}
            </td>
            <td><FieldStatus field={field} /></td>
            <td>{field.evidence.length ? field.evidence.map((e, i) => {
              const seg = offer.segments.find(s => s.locator === e.locator);
              return <div className="offers-evidence" key={`${e.locator}:${i}`}><code>{e.locator}</code> {seg ? <Quote text={seg.text} quote={e.quote} /> : e.quote}</div>;
            }) : <span className="inline-empty">{field.status === 'missing' ? 'Not stated in the document' : field.source === 'user' ? 'Entered by reviewer' : 'No verified source'}</span>}</td>
            <td>{field.kind !== 'shipments' && editing !== name && ['extracted', 'needs_review', 'corrected'].includes(offer.status) &&
              <EditButton name={name} onEdit={setEditing} />}</td>
          </tr>)}</tbody>
        </table></div>

        <aside className="offers-document" aria-label="Document text">
          <h3>Document text</h3>
          <p className="field-hint">Untrusted supplier content. Select a field to highlight its source.</p>
          <ol>{offer.segments.map(s => <li key={s.locator} className={evidenceLocators.has(s.locator) ? 'offers-hit' : ''} data-hidden={s.flags.length > 0 || undefined}>
            <code>{s.locator}</code> {s.text}{s.flags.length > 0 && <small> [{s.flags.join(', ').replaceAll('_', ' ')}]</small>}
          </li>)}</ol>
        </aside>
      </div>

      <section className="offers-scenario" aria-labelledby="offers-scenario-title">
        <h3 id="offers-scenario-title">Decision date and payment timing</h3>
        <p className="field-hint">What changes if the decision moves past this quote's validity, and how its payment terms place cash compared with another quote's. Synthetic, computed in this tab, nothing saved.</p>
        <ScenarioForm key={offer.offer_id} offer={offer} offers={samples} defaultDate={new Date().toISOString().slice(0, 10)} busy={scenario.kind === 'loading'} onRun={runScenario} />
        <ScenarioView state={scenario} offers={samples} />
      </section>

      <div className="offers-actions">
        <button className="primary-button" type="button" disabled={busy || !['extracted', 'needs_review', 'corrected'].includes(offer.status)}
          onClick={() => run(() => adapter.save(offer.offer_id, offer.version), 'Offer saved in this browser tab.')}>
          Save offer <span>→</span></button>
        <ReportDownloads label={`offer ${offer.offer_id} version ${offer.version}`} build={() => offerReport(offer, new Date(), scenario.kind === 'result' ? scenario.result : null)} />
        <p className="limit-note">Saving here is local to the fixture adapter. This preview checks the current UTC date, but cannot replace service normalization, source checks, or audit. Corrected economic values cannot be saved here until authoritative revalidation. Nothing is sent to a supplier.</p>
      </div>
    </section>}
    <footer><span>Synthetic quotes only</span><span>No supplier messages, orders or purchases</span></footer>
  </main>;
}

const sessionKey = (s: QuoteSession) => JSON.stringify([s.principal_id, s.workspace_id, s.role, s.identity_mode]);
const safeMessage = (error: unknown) => error instanceof QuoteServiceError ? error.message : 'The local quote workspace is unavailable.';
const fieldRows = (offer: ServiceOffer): [string, ServiceField][] => [
  ...Object.entries(offer.fields), ...offer.items.flatMap((item, i) => Object.entries(item).map(([name, field]) => [`items[${i}].${name}`, field] as [string, ServiceField])),
];
const currencyPrice = (value: { amount: string; currency: string } | null | undefined) => value ? `${value.amount} ${value.currency}` : 'unknown';

export function ServiceOfferReview({ offer, sourcing, selected, onSelect, onEdit, onSave, onBridge, canEdit, busy }: {
  offer: ServiceOffer; sourcing: Sourcing | null; selected: string | null; onSelect: (name: string) => void;
  onEdit: (name: string, value: unknown) => void; onSave: () => void; onBridge?: (candidate: BridgeCandidate) => void; canEdit: boolean; busy: boolean;
}) {
  const supplier = offer.fields.supplier_name?.value;
  const matching = offer.synthetic && sourcing?.synthetic && (offer.status === 'saved' || offer.status === 'used_in_plan') ? sourcing?.candidates.filter(c => c.offer_id === offer.offer_id &&
    c.offer_version === offer.version && c.document_id === offer.document.document_id &&
    c.item_index >= 0 && c.item_index < offer.items.length) ?? [] : [];
  return <section className="offers-workspace" aria-label="Local quote service offer">
    <div className="offers-head"><div><p className="eyebrow">{offer.document.filename} · {offer.document.media_type} · {offer.extractor ?? 'rules'} extractor</p>
      <h2>{typeof supplier === 'string' ? supplier : 'Supplier unknown'}</h2><p className="revision-line">Document {offer.document.document_id} · version {offer.version}</p></div>
      <div className="offers-status" data-status={offer.status}>{statusLabel(offer.status)}</div></div>
    {offer.blocking_reasons.length > 0 && <Reasons reasons={offer.blocking_reasons} title="Save is blocked until these are resolved" />}
    {offer.review_flags.length > 0 && <div className="offers-flags"><h3>Review flags</h3><ul>{offer.review_flags.map((f, i) => <li key={i}>{statusLabel(f.flag)} {f.locator && <code>{f.locator}</code>} <q>{f.text}</q></li>)}</ul></div>}
    <div className="offers-grid"><div className="table-scroll"><table className="offers-table"><caption>Service fields and source evidence</caption>
      <thead><tr><th>Field</th><th>Value</th><th>Status</th><th>Source</th><th>Review</th></tr></thead><tbody>{fieldRows(offer).map(([name, field]) => <tr key={name}>
        <th><FieldName name={name} field={field} selected={name === selected} onSelect={onSelect} /></th>
        <td>{formatValue(field.kind, field.value)}{field.status === 'conflict' && <ul className="offers-candidates">{field.candidates.filter(c => c.verified).map((c, i) => <li key={i}>{formatValue(field.kind, c.value)} {c.evidence.map(e => e.locator).join(', ')}</li>)}</ul>}</td>
        <td><FieldStatus field={field} />{field.source === 'user' && <small>Reviewer entry; no document evidence</small>}</td>
        <td>{field.evidence.length ? field.evidence.map((e, i) => <div key={i} className="offers-evidence"><code>{e.locator}</code><q>{e.quote}</q></div>) : <span className="inline-empty">{field.source === 'user' ? 'Reviewer assertion; no document evidence' : 'No verified source'}</span>}</td>
        <td>{canEdit && offer.allowed_actions.includes('correct') && field.kind !== 'shipments' && <EditButton name={name} onEdit={() => onSelect(name)} />}</td>
      </tr>)}</tbody></table></div>
      <aside className="offers-document" aria-label="Document text"><h3>Document text</h3><p className="field-hint">Untrusted supplier text. Locators reference hash-checked extracted lines or cells.</p>
        <ol>{(offer.segments ?? []).map(s => <li key={s.locator}><code>{s.locator}</code> {s.text}{s.flags.length > 0 && <small> [{s.flags.join(', ')}]</small>}</li>)}</ol></aside></div>
    {selected && canEdit && (() => { const field = fieldRows(offer).find(([name]) => name === selected)?.[1]; return field && offer.allowed_actions.includes('correct') ?
      <div className="offers-actions"><Editor key={`${offer.version}:${selected}`} field={field} busy={busy} service onCancel={() => onSelect('')} onSubmit={value => onEdit(selected, value)} /></div> : null; })()}
    <section className="offers-scenario" aria-label="Saved offer sourcing"><h3>Saved-offer sourcing</h3>
      {matching.length ? <ul>{matching.map((c, i) => <li key={i}><strong>{c.material ?? 'Material unknown'}</strong> · {c.quantity?.kg ?? 'unknown'} kg · {c.price?.type === 'index_linked'
        ? <>Index-linked; premium {currencyPrice(c.price?.premium_per_kg)} per kg; final price unknown</>
        : <>Quoted {currencyPrice(c.price?.per_kg)} per kg</>}. {c.landed_price?.per_kg ? <>Incoterm quote basis {currencyPrice(c.landed_price.per_kg)} per kg; duties and destination charges may remain excluded.</> : 'Comparable landed price unknown.'}
        {c.payment_terms?.source === 'user' && <small> Payment terms are a reviewer assertion.</small>}
        {onBridge && validBridgeCandidate(c) && <button type="button" className="secondary-button" disabled={busy}
          onClick={() => onBridge(c)}>Check host-configured shipment context for this candidate</button>}</li>)}</ul> : <p>No saved sourcing candidate for this offer. Blocking or excluded reasons stay authoritative in the service.</p>}
      {(sourcing?.excluded ?? []).filter(c => c.offer_id === offer.offer_id && c.offer_version === offer.version &&
        (c.item_index === null || c.item_index >= 0 && c.item_index < offer.items.length)).map((c, i) => <p key={i}>Excluded: {c.reason}</p>)}
    </section>
    <div className="offers-actions"><button className="primary-button" type="button" disabled={!canEdit || busy || !offer.allowed_actions.includes('save')} onClick={onSave}>Save offer →</button>
      <p className="limit-note">Local development quote service. Saving does not send an RFQ, place an order, or establish a final landed price. No fixture scenario or report is offered for service data.</p></div>
  </section>;
}

function ServiceOffersPage() {
  const navigate = useNavigate();
  const [session, setSession] = useState<QuoteSession | null>(null);
  const [offers, setOffers] = useState<ServiceOfferSummary[]>([]);
  const [offer, setOffer] = useState<ServiceOffer | null>(null);
  const [sourcing, setSourcing] = useState<Sourcing | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [message, setMessage] = useState('Checking the local development session…');
  const [busy, setBusy] = useState(false);
  const [syntheticConfirmed, setSyntheticConfirmed] = useState(false);
  const generation = useRef(0);
  const request = useRef<AbortController | null>(null);
  const sessionRef = useRef<QuoteSession | null>(null);
  function invalidate() {
    generation.current += 1; request.current?.abort(); request.current = null;
    sessionRef.current = null; setSession(null); setOffers([]); setOffer(null); setSourcing(null); setSelected(null); setBusy(false);
  }
  async function refresh(loadData: boolean) {
    invalidate(); const mine = generation.current; const controller = new AbortController(); request.current = controller;
    try {
      const next = await loadQuoteSession(controller.signal);
      if (mine !== generation.current) return;
      sessionRef.current = next; setSession(next);
      if (!loadData) { setMessage('Session refreshed. Choose Refresh workspace to load quotes.'); return; }
      const api = createQuoteService(next, () => mine === generation.current, fetch, controller.signal);
      const list = await api.list();
      const candidates = await api.sourcing();
      if (mine !== generation.current) return;
      setOffers(list); setSourcing(candidates); setMessage(list.length ? 'Choose a service offer or upload a labeled synthetic quote.' : 'No service offers in this workspace.');
    } catch (error) {
      if (mine !== generation.current) return;
      const text = safeMessage(error);
      invalidate(); setMessage(text);
      // Errors have no authenticated context. Revalidate only; do not silently reload old quote text.
      try { const fresh = await loadQuoteSession(); if (generation.current === mine + 1) { sessionRef.current = fresh; setSession(fresh); } } catch { /* retry is explicit */ }
    }
  }
  useEffect(() => {
    void refresh(true);
    const onVisible = () => { if (document.visibilityState === 'visible') void refresh(false); };
    document.addEventListener('visibilitychange', onVisible);
    return () => { document.removeEventListener('visibilitychange', onVisible); generation.current += 1; request.current?.abort(); };
  }, []);
  async function withService(operation: (api: ReturnType<typeof createQuoteService>, isCurrent: () => boolean, captured: QuoteSession, signal: AbortSignal) => Promise<void>) {
    const captured = sessionRef.current;
    if (!captured) { setMessage('Refresh the local session before continuing.'); return; }
    request.current?.abort(); const controller = new AbortController(); request.current = controller;
    const mine = ++generation.current; setBusy(true);
    try {
      const fresh = await loadQuoteSession(controller.signal);
      if (mine !== generation.current || sessionKey(fresh) !== sessionKey(captured)) throw new QuoteServiceError('identity_changed', 'The session changed. Refresh the workspace.');
      const api = createQuoteService(captured, () => mine === generation.current && sessionRef.current === captured, fetch, controller.signal);
      await operation(api, () => mine === generation.current && !controller.signal.aborted && sessionRef.current === captured, captured, controller.signal);
    } catch (error) {
      if (mine === generation.current) { const text = safeMessage(error); invalidate(); setMessage(text); try { const fresh = await loadQuoteSession(); if (generation.current === mine + 1) { sessionRef.current = fresh; setSession(fresh); } } catch { /* explicit retry */ } }
    } finally { if (mine === generation.current) setBusy(false); }
  }
  async function open(id: string) {
    await withService(async (api, isCurrent) => {
      const detail = await api.detail(id);
      const extraction = await api.extraction(detail.extraction_id);
      const bound = await bindOfferExtraction(detail, extraction, isCurrent);
      const currentSourcing = await api.sourcing();
      setOffer(bound); setSourcing(currentSourcing); setSelected(null); setMessage('Service offer loaded.');
    });
  }
  async function upload(file: File) {
    if (file.size > 8 * 1024 * 1024 || !/\.(pdf|xlsx|csv)$/i.test(file.name)) { setMessage('Choose a PDF, XLSX or CSV of at most 8 MiB.'); return; }
    await withService(async (api, isCurrent) => {
      const bytes = new Uint8Array(await file.arrayBuffer());
      let binary = ''; for (let i = 0; i < bytes.length; i += 32768) binary += String.fromCharCode(...bytes.subarray(i, i + 32768));
      const result = await api.upload(file.name, btoa(binary));
      const detail = await api.detail(result.offer.offer_id);
      const extraction = await api.extraction(result.extraction_id);
      const bound = await bindOfferExtraction(detail, extraction, isCurrent);
      const list = await api.list(); const currentSourcing = await api.sourcing();
      setOffer(bound); setSourcing(currentSourcing); setSelected(null); setMessage(result.deduplicated ? 'Existing synthetic upload opened.' : 'Synthetic quote uploaded for review.');
      setOffers(list);
    });
  }
  async function mutate(kind: 'correct' | 'save', name?: string, value?: unknown) {
    if (!offer || sessionRef.current?.role !== 'editor') return;
    const current = offer;
    await withService(async (api, isCurrent) => {
      const key = crypto.randomUUID();
      if (kind === 'correct') await api.correct(current.offer_id, name!, value, current.version, key);
      else await api.save(current.offer_id, current.version, key);
      const detail = await api.detail(current.offer_id);
      const extraction = await api.extraction(detail.extraction_id);
      const bound = await bindOfferExtraction(detail, extraction, isCurrent);
      const list = await api.list(); const currentSourcing = await api.sourcing();
      setOffer(bound); setSelected(null);
      setOffers(list); setSourcing(currentSourcing);
      setMessage(kind === 'save' ? 'Offer saved in the local service.' : 'Reviewer correction saved and revalidated by the service.');
    });
  }
  async function bridge(candidate: BridgeCandidate) {
    const current = offer;
    if (!current || !sourcing || (current.status !== 'saved' && current.status !== 'used_in_plan') ||
        candidate.offer_id !== current.offer_id || candidate.offer_version !== current.version ||
        candidate.document_id !== current.document.document_id || candidate.item_index >= current.items.length ||
        !sourcing.candidates.some(c => c.candidate_id === candidate.candidate_id && c.offer_id === candidate.offer_id &&
          c.offer_version === candidate.offer_version && c.item_index === candidate.item_index && c.document_id === candidate.document_id)) return;
    await withService(async (_api, isCurrent, captured, signal) => {
      const response = await getQuoteShipmentBridge(captured, candidate, isCurrent, signal);
      const fresh = await loadQuoteSession(signal);
      if (!isCurrent() || sessionKey(fresh) !== sessionKey(captured)) throw new QuoteServiceError('identity_changed', 'The session changed. Refresh the workspace.');
      const handoff = stageHandoff(response, captured);
      if (!isCurrent()) throw new QuoteServiceError('identity_changed', 'The session changed. Refresh the workspace.');
      navigate(handoffUrl(handoff.triple), { state: handoff });
    });
  }
  return <main className="shell offers-shell"><WorkspaceHeader section="OFFERS" status="Synthetic · local quote service · development only" />
    <section className="recovery-intro"><p className="eyebrow">Quote intelligence · local service</p><h1>Review supplier quotes</h1>
      <p>Service values, evidence and blocking reasons are shown as returned. Reviewer entries are separate from document evidence.</p></section>
    <div className="offers-adapter" role="note"><strong>LOCAL DEVELOPMENT SERVICE · GATEWAY ROUTE MAY BE DISABLED</strong>
      <span>Same-origin session required. This browser has no bearer token or signing key. No source text is shown after an identity or service error.</span></div>
    <div className="offers-load"><div><strong>{session ? `${session.role} · workspace ${session.workspace_id}` : 'Session unavailable'}</strong><p>{message}</p>
      <button className="secondary-button" type="button" onClick={() => void refresh(true)} disabled={busy}>Refresh workspace</button></div>
      <div><label className="field"><input type="checkbox" checked={syntheticConfirmed} onChange={e => setSyntheticConfirmed(e.target.checked)} /> I confirm this is synthetic test material.</label>
      <label className="field"><span className="field-label">Upload synthetic PDF, XLSX or CSV</span><input type="file" accept=".pdf,.xlsx,.csv" disabled={!syntheticConfirmed || !session || session.role !== 'editor' || busy} onChange={e => { const f = e.target.files?.[0]; e.target.value = ''; if (f) void upload(f); }} /></label></div></div>
    <div className="offers-actions"><h3>Workspace offers</h3>{offers.length ? <ul>{offers.map(o => <li key={o.offer_id}><button className="text-button" type="button" onClick={() => void open(o.offer_id)} disabled={busy}>{o.supplier_name ?? 'Supplier unknown'} · {o.filename} · {o.status} · version {o.version}</button></li>)}</ul> : <p>No offers loaded.</p>}</div>
    {offer && <ServiceOfferReview offer={offer} sourcing={sourcing} selected={selected} onSelect={setSelected} onEdit={(name, value) => void mutate('correct', name, value)} onSave={() => void mutate('save')} onBridge={candidate => void bridge(candidate)} canEdit={session?.role === 'editor'} busy={busy} />}
    <footer><span>Service mode · local development</span><span>No supplier messages, orders or purchases</span></footer></main>;
}

export function OffersPage() {
  const [mode, setMode] = useState<'fixture' | 'service'>('fixture');
  return <><div className="offers-adapter" role="group" aria-label="Quote workspace mode">
    <button className="secondary-button" type="button" aria-pressed={mode === 'fixture'} onClick={() => setMode('fixture')}>Synthetic fixtures</button>
    <button className="secondary-button" type="button" aria-pressed={mode === 'service'} onClick={() => setMode('service')}>Local quote service</button>
  </div>{mode === 'fixture' ? <FixtureOffersPage /> : <ServiceOffersPage />}</>;
}
