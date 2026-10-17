import type { RiskAlert } from './demo-api';

// Only authored operational risk templates are adjusted for display. Public news and
// the canonical alert, evidence, metrics, and action message remain untouched.
const replacements: [string, string][] = [
  ['Stated synthetic stock', 'Stated stock'],
  ['buyer synthetic stock assumption', 'buyer stock assumption'],
  ['fictional profile stock', 'profile stock'],
  ['fictional daily use', 'daily use'],
  ['stated fictional use and lead assumptions', 'stated use and lead assumptions'],
  ['fictional supplier option', 'listed supplier option'],
  ['fictional order and supplier assumptions', 'stated order and supplier assumptions'],
  ['fictional order deadline', 'stated order deadline'],
  ['synthetic order deadline', 'stated order deadline'],
  ['synthetic lead-time exposure', 'lead-time exposure'],
  ['recorded synthetic recovery', 'recorded recovery'],
  ['synthetic projection', 'planning projection'],
  ['fictional offers', 'listed offers'],
  ['fictional supplier lead', 'listed supplier lead'],
  ['Review synthetic alternatives', 'Review alternatives'],
  ['Load synthetic alternatives', 'Load alternatives'],
  ['sample quotes for review', 'quotes for review'],
];

export function operationalRiskText(alert: RiskAlert, value: string): string {
  if (alert.provenance === 'observed_news') return value;
  return replacements.reduce((text, [from, to]) => text.replaceAll(from, to), value);
}
