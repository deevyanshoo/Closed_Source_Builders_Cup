// /dispatch "Local dispatch service" mode (#77): the same read models as the fixture adapter, served by the local
// Python service through the gateway's /v2/workspaces/{workspace_id}/dispatch/... routes on the current session.
// Every answer must be the gateway wrapper for this session's workspace, labelled synthetic, with synthetic data.
// The cutoff is the fixture's, so both modes describe the same moment. Read only; nothing is cached across sessions.
import type { SessionResponse } from '../../../contracts/generated/types';
import { DispatchError, dispatchFixtureInfo, emptyHazards, emptyRouteEvidence, readable, readableReplay } from './dispatch';
import type { AffectedResponse, DispatchAdapter, EvidenceResponse, HazardResponse, ListResponse, ReplayResponse,
  ReplaySamplesResponse, TimelineResponse } from './dispatch';
import { fetchSession } from './state';

type Options = { transport?: typeof fetch; loadSession?: () => Promise<SessionResponse>; cutoff?: string };
type Answer = { data: Record<string, unknown> } | { refusal: Record<string, unknown> };
const WRAPPER_KEYS = 'contract_version,data,source,synthetic,workspace_id';
const isObject = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);

export function createDispatchServiceAdapter({ transport = (input, init) => fetch(input, init), loadSession = () => fetchSession(),
  cutoff = dispatchFixtureInfo.asOf }: Options = {}): DispatchAdapter {
  let session: Promise<SessionResponse> | null = null;
  let locations: Promise<Map<string, string[]>> | null = null;

  async function call(path: string, params: [string, string][] = []): Promise<Answer> {
    let identity: SessionResponse;
    try { identity = await (session ??= loadSession()); } catch {
      session = null;
      throw new DispatchError('The local session is unavailable. Check the gateway session.', 'unavailable');
    }
    const query = new URLSearchParams(params).toString();
    const url = `/v2/workspaces/${encodeURIComponent(identity.workspace_id)}/dispatch/${path}${query ? `?${query}` : ''}`;
    let response: Response;
    try { response = await transport(url, { headers: { Accept: 'application/json' } }); } catch {
      throw new DispatchError('The local dispatch service could not be reached.', 'unavailable');
    }
    let body: unknown;
    try { body = await response.json(); } catch { throw new DispatchError('The local dispatch service returned an invalid answer.', 'unavailable'); }
    if (response.ok) {
      if (!isObject(body) || Object.keys(body).sort().join(',') !== WRAPPER_KEYS || body.contract_version !== '0.2.0'
          || body.workspace_id !== identity.workspace_id || body.synthetic !== true || body.source !== 'local_python_service'
          || !isObject(body.data) || body.data.synthetic !== true)
        throw new DispatchError('The local dispatch service returned an answer that is not labelled for this workspace.', 'unavailable');
      return { data: body.data };
    }
    if (response.status === 401 || response.status === 403) throw new DispatchError('The local session is unavailable. Check the gateway session.', 'unavailable');
    if (response.status === 404) throw new DispatchError('Not found in the synthetic dispatch data.', 'not_found');
    if (response.status === 503) throw new DispatchError('The local dispatch service is not enabled on this gateway.', 'unavailable');
    if (response.status === 422 && isObject(body) && isObject(body.data) && body.data.synthetic === true) return { refusal: body.data };
    throw new DispatchError('The local dispatch service is unavailable.', 'unavailable');
  }

  const at: [string, string][] = [['as_of', cutoff], ['now', cutoff]];
  const data = (answer: Answer) => {
    if ('refusal' in answer) throw new DispatchError('The local dispatch service refused this request.', 'unavailable');
    return answer.data;
  };
  const list = async () => readable(data(await call('shipments', at)) as unknown as ListResponse);
  const keysFor = async (shipmentId: string) => {
    locations ??= list().then(value => new Map(value.shipments.map(row => [row.shipment_id, row.location_keys ?? []])))
      .catch(error => { locations = null; throw error; });
    return (await locations).get(shipmentId) ?? [];
  };

  return {
    source: 'local_dispatch_service',
    synthetic: true,
    async listShipments(): Promise<ListResponse> {
      const value = await list();
      locations = Promise.resolve(new Map(value.shipments.map(row => [row.shipment_id, row.location_keys ?? []])));
      return value;
    },
    async timeline(shipmentId: string): Promise<TimelineResponse> {
      return readable(data(await call(`shipments/${encodeURIComponent(shipmentId)}`, at)) as unknown as TimelineResponse);
    },
    async affected(eventId: string): Promise<AffectedResponse> {
      return readable(data(await call(`events/${encodeURIComponent(eventId)}/affected`, at)) as unknown as AffectedResponse);
    },
    async routeEvidence(shipmentId: string): Promise<EvidenceResponse> {
      const keys = await keysFor(shipmentId);
      if (!keys.length) return emptyRouteEvidence(cutoff);
      return readable(data(await call('route-evidence', [...at, ...keys.map(k => ['location', k] as [string, string])])) as unknown as EvidenceResponse);
    },
    async replaySamples(): Promise<ReplaySamplesResponse> {
      const value = data(await call('replay/samples'));
      if (!Array.isArray(value.samples)) throw new DispatchError('Replay samples are unavailable.', 'unavailable');
      return structuredClone({ notice: value.notice, samples: value.samples }) as ReplaySamplesResponse;
    },
    async replay(sampleId: string): Promise<ReplayResponse> {
      const answer = await call('replay', [['sample', sampleId], ...at]);
      return readableReplay(('refusal' in answer ? answer.refusal : answer.data) as unknown as ReplayResponse, sampleId);
    },
    async hazards(shipmentId: string): Promise<HazardResponse> {
      const keys = await keysFor(shipmentId);
      if (!keys.length) return emptyHazards(cutoff);
      return readable(data(await call('hazards', [...at, ...keys.map(k => ['location', k] as [string, string])])) as unknown as HazardResponse);
    },
  };
}
