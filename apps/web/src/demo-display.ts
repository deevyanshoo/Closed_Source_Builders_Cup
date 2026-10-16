/** Display-only label. Records and action arguments keep the server value. */
export function supplierDisplayName(name: string): string {
  return name.endsWith(' [SYNTHETIC]') ? name.slice(0, -' [SYNTHETIC]'.length) : name;
}

export function displaySupplierMentions(text: string, canonicalNames: string[]): string {
  return canonicalNames.reduce((shown, raw) => raw.endsWith(' [SYNTHETIC]') ?
    shown.replaceAll(raw, supplierDisplayName(raw)) : shown, text);
}

/** Kept so the existing call compiles; the ZOORQ shell (index.html) owns the browser title, so nothing changes it. */
export function applyDemoTitle(_doc: { title: string }): void {
  // intentionally no change (review, 16 Oct): the legacy "Closed Source | Hosted demo" title is dropped
}

export const COMPARISON_PRICE_HEADER = 'Comparison unit price';

/** The planner's unit price after unit and FX normalisation, in the plan's reporting currency. */
export function comparisonUnitPriceText(alternative: { unit_price_per_unit?: string | null; unit_price_per_kg?: string | null },
  plan: { reporting_currency: string; quantity_unit?: string }): string {
  const value = alternative.unit_price_per_unit ?? alternative.unit_price_per_kg;
  if (value == null) return 'Unavailable';
  return `${value} ${plan.reporting_currency}/${plan.quantity_unit ?? 'kg'} (reporting currency, after unit and FX normalisation)`;
}

/** The supplier's own quoted price, currency and unit, from the reviewed first priced item. */
export function originalQuotePriceText(offer: { items?: Record<string, { value?: unknown } | undefined>[] }): string {
  const pricing = offer.items?.[0]?.pricing?.value as { unit_price?: { amount?: string; currency?: string }; per_unit?: string } | undefined;
  if (!pricing?.unit_price?.amount || !pricing.unit_price.currency || !pricing.per_unit) return 'Original quote price not stated';
  return `${pricing.unit_price.amount} ${pricing.unit_price.currency} per ${pricing.per_unit} as quoted`;
}

/** Display-only stock cover: one decimal, prefixed "about" when rounding changed the value. */
export function stockCoverText(days: string): string {
  if (!days.includes('.')) return days;
  const rounded = roundDecimalText(days, 1);
  const exact = days.replace(/0+$/, '').replace(/\.$/, '');
  return rounded.replace(/0+$/, '').replace(/\.$/, '') === exact ? rounded : `about ${rounded}`;
}

/** Threshold logic on the exact decimal text, never on the rounded display value. */
export function coverReachesLead(days: string, leadDays: number): boolean {
  const [whole, fraction = ''] = days.split('.');
  const scale = 10n ** BigInt(fraction.length);
  return BigInt(whole + fraction) <= BigInt(leadDays) * scale;
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
