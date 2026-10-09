// Shared helpers for the downloadable offer and dispatch reports. Everything exported is synthetic, and
// cell text can come from untrusted documents, so CSV cells are quoted and spreadsheet formulas neutralised.
export const REPORT_NOTICE =
  'SYNTHETIC DATA from the local fixture adapter. Not live supplier, shipment, port or weather data; not for operational use.';

export type Report = { basename: string; json: string; csv: string };
type Cell = string | number | boolean | null | undefined;

export function csvCell(value: Cell): string {
  if (value === null || value === undefined) return '';
  let text = String(value);
  if (/^[=+\-@\t\r]/.test(text)) text = `'${text}`;
  return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

export function toCsv(header: string[], rows: Cell[][]): string {
  return [header, ...rows].map(row => row.map(csvCell).join(',')).join('\r\n') + '\r\n';
}

/** Browser-only: save text as a file. */
export function download(filename: string, text: string, type: string): void {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

export function downloadReport(report: Report, format: 'json' | 'csv'): void {
  if (format === 'json') download(`${report.basename}.json`, report.json, 'application/json');
  else download(`${report.basename}.csv`, report.csv, 'text/csv;charset=utf-8');
}
