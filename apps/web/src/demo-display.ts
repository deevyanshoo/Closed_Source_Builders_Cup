/** Display-only label. Records and action arguments keep the server value. */
export function supplierDisplayName(name: string): string {
  return name.endsWith(' [SYNTHETIC]') ? name.slice(0, -' [SYNTHETIC]'.length) : name;
}

export function displaySupplierMentions(text: string, canonicalNames: string[]): string {
  return canonicalNames.reduce((shown, raw) => raw.endsWith(' [SYNTHETIC]') ?
    shown.replaceAll(raw, supplierDisplayName(raw)) : shown, text);
}

export const DEMO_TITLE = 'Closed Source | Hosted demo';

export function applyDemoTitle(doc: { title: string }): void {
  doc.title = DEMO_TITLE;
}

/** Round a plain decimal string half up for display, using integer arithmetic. Other text is returned unchanged. */
export function roundDecimalText(text: string, places: number): string {
  const match = /^(\d+)(?:\.(\d+))?$/.exec(text);
  if (!match) return text;
  const fraction = (match[2] ?? '').padEnd(places + 1, '0');
  const scaled = BigInt(match[1] + fraction.slice(0, places)) + (Number(fraction[places]) >= 5 ? 1n : 0n);
  const digits = scaled.toString().padStart(places + 1, '0');
  return places ? `${digits.slice(0, -places)}.${digits.slice(-places)}` : digits;
}
