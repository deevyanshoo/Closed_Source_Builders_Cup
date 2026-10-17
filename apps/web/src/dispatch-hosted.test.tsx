import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { DemoPage } from './demo-page';
import { readSupplyLocation, readWorkspaceLocation, workspaceSearch } from './zoorq-workspace';

describe('hosted Supply Shipments destination', () => {
  it('resolves the server redirect destination into the current Supply tab', () => {
    const redirected = '/demo?view=inventory&supply=shipments';
    const search = new URL(redirected, 'https://example.invalid').search;
    expect(readWorkspaceLocation(search).view).toBe('inventory');
    expect(readSupplyLocation(search)).toBe('shipments');
    expect(workspaceSearch('?view=inventory&supply=shipments', 'procurement', 'compare')).toBe('?view=procurement&stage=compare');
  });

  it('does not advertise the legacy hosted dispatch page in the current demo header', () => {
    const html = renderToStaticMarkup(<DemoPage />);
    expect(html).not.toContain('/dispatch?from=demo');
    expect(html).not.toContain('>Dispatch</a>');
  });
});
