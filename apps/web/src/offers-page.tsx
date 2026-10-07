import { useMemo, useRef, useState } from 'react';
import type { ChangeEvent, FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { createFixtureAdapter, dataAge, fieldEntries, fixtureInfo, formatValue, OffersError } from './offers';
import type { BlockingReason, Field, Offer } from './offers';
import './offers.css';

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

export function Editor({ field, onSubmit, onCancel, busy }: { field: Field; onSubmit: (value: unknown) => void; onCancel: () => void; busy: boolean }) {
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
  else if (field.kind === 'material') inputs = <label className="field"><span className="field-label">Material</span><select value={text} onChange={e => setText(e.target.value)}><option value="">Choose</option>{fixtureInfo.materials.map(m => <option key={m}>{m}</option>)}</select></label>;
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

export function OffersPage() {
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

  function show(next: Offer) {
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

  const evidenceLocators = new Set(selected && offer ? fieldEntries(offer).find(([n]) => n === selected)?.[1].evidence.map(e => e.locator) : []);

  return <main className="shell">
    <header className="masthead">
      <div className="brand">CLOSED<span>SOURCE</span><sup>OFFERS</sup></div>
      <div className="masthead-meta">Synthetic · local adapter<span className="red-dot">●</span></div>
      <Link className="recovery-back" to="/planning">Planning</Link>
    </header>

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
            <th scope="row">{fieldLabel(name)}{field.critical && <small>critical</small>}</th>
            <td>{editing === name
              ? <Editor field={field} busy={busy} onCancel={() => setEditing(null)} onSubmit={value => run(() => adapter.correct(offer.offer_id, name, value, offer.version), `Corrected ${fieldLabel(name)}.`)} />
              : formatValue(field.kind, field.value)}
              {field.status === 'conflict' && <ul className="offers-candidates">{field.candidates.filter(c => c.verified).map((c, i) => <li key={i}>{formatValue(field.kind, c.value)} <code>{c.locators.join(', ')}</code></li>)}</ul>}
              {field.status === 'unverified' && field.reasons.length > 0 && <small className="offers-reason">{field.reasons.slice(0, 3).join(', ').replaceAll('_', ' ')}</small>}
            </td>
            <td><span className="offers-chip" data-status={field.status}>{field.status}</span>{field.source === 'user' && <small>reviewer entry</small>}</td>
            <td>{field.evidence.length ? field.evidence.map((e, i) => {
              const seg = offer.segments.find(s => s.locator === e.locator);
              return <div className="offers-evidence" key={`${e.locator}:${i}`}><code>{e.locator}</code> {seg ? <Quote text={seg.text} quote={e.quote} /> : e.quote}</div>;
            }) : <span className="inline-empty">{field.status === 'missing' ? 'Not stated in the document' : field.source === 'user' ? 'Entered by reviewer' : 'No verified source'}</span>}</td>
            <td>{field.kind !== 'shipments' && editing !== name && ['extracted', 'needs_review', 'corrected'].includes(offer.status) &&
              <button className="text-button" type="button" onClick={e => { e.stopPropagation(); setEditing(name); }}>Edit</button>}</td>
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

      <div className="offers-actions">
        <button className="primary-button" type="button" disabled={busy || !['extracted', 'needs_review', 'corrected'].includes(offer.status)}
          onClick={() => run(() => adapter.save(offer.offer_id, offer.version), 'Offer saved in this browser tab.')}>
          Save offer <span>→</span></button>
        <p className="limit-note">Saving here is local to the fixture adapter. This preview checks the current UTC date, but cannot replace service normalization, source checks, or audit. Corrected economic values cannot be saved here until authoritative revalidation. Nothing is sent to a supplier.</p>
      </div>
    </section>}
    <footer><span>Synthetic quotes only</span><span>No supplier messages, orders or purchases</span></footer>
  </main>;
}
