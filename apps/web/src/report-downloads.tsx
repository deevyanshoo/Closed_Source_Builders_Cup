import { downloadReport } from './report';
import type { Report } from './report';

/** JSON and CSV download buttons; the report is built only when clicked. */
export function ReportDownloads({ label, build }: { label: string; build: () => Report }) {
  return <p className="report-downloads">
    <button type="button" className="secondary-button" aria-label={`Download ${label} as JSON`} onClick={() => downloadReport(build(), 'json')}>Download JSON</button>
    <button type="button" className="secondary-button" aria-label={`Download ${label} as CSV`} onClick={() => downloadReport(build(), 'csv')}>Download CSV</button>
    <small>Built in this browser; nothing is sent. Rows retain their source labels.</small>
  </p>;
}
