/** Display-only label. Records and action arguments keep the server value. */
export function supplierDisplayName(name: string): string {
  return name.endsWith(' [SYNTHETIC]') ? name.slice(0, -' [SYNTHETIC]'.length) : name;
}

export function displaySupplierMentions(text: string, canonicalNames: string[]): string {
  return canonicalNames.reduce((shown, raw) => raw.endsWith(' [SYNTHETIC]') ?
    shown.replaceAll(raw, supplierDisplayName(raw)) : shown, text);
}

/** RFQ preview only. The stored body and downloaded document remain canonical. */
export function displayRfqDraftBody(body: string, canonicalNames: string[]): string {
  return displaySupplierMentions(body.replace(/^SYNTHETIC TEST DOCUMENT — (?=UNSENT (?:BRIDGE )?REQUEST FOR QUOTATION DRAFT(?:\r?\n|$))/, ''), canonicalNames);
}
