import { downloadReport } from './report';
import type { Report } from './report';

/** JSON and CSV download buttons; the report is built only when clicked. */
export function ReportDownloads({ label, build }: { label: string; build: () => Report }) {
  return <p className="report-downloads">
    <button type="button" className="secondary-button" aria-label={`Download ${label} as synthetic JSON`} onClick={() => downloadReport(build(), 'json')}>Download synthetic JSON</button>
    <button type="button" className="secondary-button" aria-label={`Download ${label} as synthetic CSV`} onClick={() => downloadReport(build(), 'csv')}>Download synthetic CSV</button>
    <small>Labelled synthetic in every row. Built in this browser; nothing is sent.</small>
  </p>;
}
