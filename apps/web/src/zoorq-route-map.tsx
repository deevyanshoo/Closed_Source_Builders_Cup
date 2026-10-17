import { useEffect, useReducer, useRef, useState } from 'react';
import './zoorq-route-map.css';

export type RoutePoint = { lat: number; lng: number };
export type RouteSegment = {
  id: string; kind: 'vendor' | 'road' | 'origin_port' | 'sea' | 'destination_port' | 'buyer' | 'transport';
  status: 'supported' | 'approximate' | 'unavailable'; label: string;
  geometry: RoutePoint[] | null; source: string; precision: string;
};
export type RouteDisplayView = {
  schema: 1; company_id: string; route_id: string; material_id: string;
  buyer_location_index: number; supplier_id: string | null; offer_id: string | null;
  status: 'supported' | 'partial' | 'unavailable'; segments: RouteSegment[]; limitations: string[];
  itinerary?: { origin: string; via: string[]; destination: string; mode: 'sea' | 'land' | 'unknown'; display: 'schematic' | 'map' };
  coverage?: { declared_routes: number; declared_route_material_pairs: number; mapped_route_material_pairs: number; schematic_route_material_pairs: number; options?: {route_id:string;material_id:string;mode:'sea'|'land'|'unknown';display:'map'|'schematic'}[] };
};
export type RoutePreview = { kind: 'disruption' | 'recovery'; label: string };
export type ZoorqRouteMapProps = {
  view: RouteDisplayView; apiKey?: string | null; reducedMotion?: boolean;
  preview?: RoutePreview | null; onAsk?: (prompt: string) => void;
};

type MapShape = { fitBounds(bounds: { south: number; north: number; west: number; east: number }): void };
type PolylineShape = { setMap(map: MapShape | null): void };
type MarkerShape = { setMap(map: MapShape | null): void; setPosition(point: RoutePoint): void; setLabel(label: string): void };
export type RouteMaps = {
  Map: new (host: HTMLElement, options: Record<string, unknown>) => MapShape;
  Polyline: new (options: Record<string, unknown>) => PolylineShape;
  Marker: new (options: Record<string, unknown>) => MarkerShape;
};
type RouteWindow = Window & { google?: { maps?: RouteMaps }; __zoorqRouteMapsReady?: () => void; gm_authFailure?: () => void };

let pendingMaps: Promise<RouteMaps> | null = null;
let pendingKey: string | null = null;
let mapsAuthFailed = false;
let activeAttempt = 0;
let attemptSequence = 0;
let cancelPending: (() => void) | null = null;
let mapLeases = 0;
const authFailureListeners = new Set<() => void>();

/** One Maps JavaScript load per browser page. A failed load can be retried. */
export function loadRouteMaps(apiKey: string | null | undefined, timeoutMs = 12000): Promise<RouteMaps> {
  if (!apiKey?.trim()) return Promise.reject(new Error('A restricted browser map key is required.'));
  if (typeof window === 'undefined' || typeof document === 'undefined') return Promise.reject(new Error('Map requires a browser.'));
  if (mapsAuthFailed) return Promise.reject(new Error('Map key authorization failed.'));
  const routeWindow = window as RouteWindow;
  if (pendingKey && pendingKey !== apiKey) return Promise.reject(new Error('A different browser map key is already in use.'));
  if (routeWindow.google?.maps?.Map) return Promise.resolve(routeWindow.google.maps);
  if (pendingMaps) return pendingMaps;
  pendingKey = apiKey;
  let attachScript = () => {};
  const loading = new Promise<RouteMaps>((resolve, reject) => {
    const attempt = ++attemptSequence;
    activeAttempt = attempt;
    const callbackName = `__zoorqRouteMapsReady_${attempt}`;
    const callbacks = routeWindow as unknown as Record<string, unknown>;
    const script = document.createElement('script');
    const previousAuthFailure = routeWindow.gm_authFailure;
    let settled = false;
    const ready = () => finish();
    const authFailure = () => {
      if (activeAttempt !== attempt || pendingKey !== apiKey) return;
      // An explicitly different executing script belongs to an older attempt.
      // Google's async auth callback has no currentScript, so the active
      // attempt for this key must still accept it as an authorization failure.
      if (attempt > 1 && document.currentScript && document.currentScript !== script) return;
      mapsAuthFailed = true;
      finish(new Error('Map key authorization failed.'));
      authFailureListeners.forEach(listener => listener());
      previousAuthFailure?.();
    };
    const finish = (error?: Error) => {
      if (settled || activeAttempt !== attempt) return;
      settled = true;
      clearTimeout(timer);
      if (callbacks[callbackName] === ready) delete callbacks[callbackName];
      cancelPending = null;
      if (error) {
        if (routeWindow.gm_authFailure === authFailure) routeWindow.gm_authFailure = previousAuthFailure;
        script.remove();
        pendingMaps = null;
        pendingKey = null;
        activeAttempt = 0;
        reject(error);
      } else if (routeWindow.google?.maps?.Map) {
        resolve(routeWindow.google.maps);
      } else {
        if (routeWindow.gm_authFailure === authFailure) routeWindow.gm_authFailure = previousAuthFailure;
        script.remove();
        pendingMaps = null;
        pendingKey = null;
        activeAttempt = 0;
        reject(new Error('Maps JavaScript did not become available.'));
      }
    };
    const timer = setTimeout(() => finish(new Error('Map loading timed out.')), timeoutMs);
    cancelPending = () => finish(new Error('Map load cancelled after unmount.'));
    callbacks[callbackName] = ready;
    routeWindow.gm_authFailure = authFailure;
    script.async = true;
    script.defer = true;
    script.src = `https://maps.googleapis.com/maps/api/js?key=${encodeURIComponent(apiKey)}&v=weekly&callback=${callbackName}`;
    script.onerror = () => finish(new Error('Map script failed to load.'));
    attachScript = () => {
      try { document.head.appendChild(script); }
      catch { finish(new Error('Map script could not be attached.')); }
    };
  });
  pendingMaps = loading;
  attachScript();
  return loading;
}

/** A mounted consumer releases the shared loader when its context disappears. */
export function acquireRouteMaps(apiKey: string) {
  mapLeases += 1;
  const promise = loadRouteMaps(apiKey);
  let released = false;
  return { promise, release() {
    if (released) return;
    released = true;
    mapLeases -= 1;
    if (mapLeases === 0) cancelPending?.();
  } };
}

const movingSegments = (view: RouteDisplayView) => view.segments.filter(segment =>
  (segment.kind === 'road' || segment.kind === 'sea') && segment.status !== 'unavailable' && (segment.geometry?.length ?? 0) >= 2);

/** Progress is a visual scrub value, never a measured journey distance. */
export function routePosition(view: RouteDisplayView, progress: number): { kind: 'road' | 'sea'; point: RoutePoint } | null {
  const segments = movingSegments(view);
  if (!segments.length) return null;
  const safe = Math.min(1, Math.max(0, Number.isFinite(progress) ? progress : 0));
  const scaled = safe * segments.length;
  const index = Math.min(segments.length - 1, Math.floor(scaled));
  const segment = segments[index];
  const points = segment.geometry!;
  if (safe === 1) return { kind: segment.kind as 'road' | 'sea', point: points[points.length - 1] };
  const local = index === segments.length - 1 && safe === 1 ? 1 : scaled - index;
  const step = local * (points.length - 1);
  const first = Math.min(points.length - 2, Math.floor(step));
  const fraction = step - first;
  const a = points[first], b = points[first + 1];
  const longitudeDelta = ((b.lng - a.lng + 540) % 360) - 180;
  const lng = ((a.lng + longitudeDelta * fraction + 540) % 360) - 180;
  return { kind: segment.kind as 'road' | 'sea', point: { lat: a.lat + (b.lat - a.lat) * fraction, lng } };
}

export function advanceRouteProgress(progress: number, elapsedMs: number, speed: number): number {
  if (!Number.isFinite(progress) || !Number.isFinite(elapsedMs) || !Number.isFinite(speed) || elapsedMs <= 0 || speed <= 0) return progress;
  return (progress + elapsedMs * speed / 18000) % 1;
}

export type RoutePlaybackState = { progress: number; speed: number; playing: boolean };
export type RoutePlaybackAction =
  | { type: 'play'; allowed: boolean } | { type: 'pause' } | { type: 'reset' }
  | { type: 'speed'; speed: number } | { type: 'scrub'; progress: number }
  | { type: 'tick'; elapsedMs: number };

/** The UI and focused tests share the same local, display-only control logic. */
export function routePlaybackReducer(state: RoutePlaybackState, action: RoutePlaybackAction): RoutePlaybackState {
  switch (action.type) {
    case 'play': return action.allowed ? { ...state, playing: true } : state;
    case 'pause': return { ...state, playing: false };
    case 'reset': return { progress: 0, speed: 1, playing: false };
    case 'speed': return [0.5, 1, 2].includes(action.speed) ? { ...state, speed: action.speed } : state;
    case 'scrub': return Number.isFinite(action.progress)
      ? { ...state, progress: Math.min(1, Math.max(0, action.progress)) } : state;
    case 'tick': return state.playing
      ? { ...state, progress: advanceRouteProgress(state.progress, action.elapsedMs, state.speed) } : state;
  }
}

export function prefersRouteReducedMotion(): boolean {
  return typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
}

function wrapLongitude(lng: number): number {
  return ((lng + 180) % 360 + 360) % 360 - 180;
}

/** Split at the dateline so a non-geodesic line never draws the long way around. */
function splitAtDateline(points: RoutePoint[]): RoutePoint[][] {
  if (points.length < 2) return [];
  const parts: RoutePoint[][] = [];
  let part: RoutePoint[] = [points[0]];
  for (let index = 1; index < points.length; index += 1) {
    const from = points[index - 1];
    const to = points[index];
    const delta = ((to.lng - from.lng + 540) % 360) - 180;
    const unwrappedEnd = from.lng + delta;
    if (unwrappedEnd > 180 || unwrappedEnd < -180) {
      const edge = unwrappedEnd > 180 ? 180 : -180;
      const fraction = (edge - from.lng) / delta;
      const latitude = from.lat + (to.lat - from.lat) * fraction;
      part.push({ lat: latitude, lng: edge });
      parts.push(part);
      part = [{ lat: latitude, lng: -edge }, to];
    } else {
      part.push(to);
    }
  }
  if (part.length >= 2) parts.push(part);
  return parts;
}

/** Longitude bounds are the smallest circular interval covering shown points. */
function minimalCircularBounds(points: RoutePoint[]) {
  const longitudes = points.map(point => ((point.lng % 360) + 360) % 360).sort((a, b) => a - b);
  let gapIndex = 0;
  let largestGap = -1;
  longitudes.forEach((value, index) => {
    const next = index + 1 < longitudes.length ? longitudes[index + 1] : longitudes[0] + 360;
    if (next - value > largestGap) { largestGap = next - value; gapIndex = index; }
  });
  return {
    south: Math.min(...points.map(point => point.lat)),
    north: Math.max(...points.map(point => point.lat)),
    west: wrapLongitude(longitudes[(gapIndex + 1) % longitudes.length]),
    east: wrapLongitude(longitudes[gapIndex]),
  };
}

/** Owns and disposes only the overlays created for one company/route context. */
export function mountRouteMap(host: HTMLElement, view: RouteDisplayView, maps: RouteMaps) {
  const map = new maps.Map(host, { center: { lat: 15, lng: 20 }, zoom: 3, disableDefaultUI: true, gestureHandling: 'cooperative', clickableIcons: false });
  const lines: PolylineShape[] = [];
  const shownPoints: RoutePoint[] = [];
  for (const segment of view.segments) {
    if (!segment.geometry?.length || segment.status === 'unavailable') continue;
    shownPoints.push(...segment.geometry);
    if (segment.kind !== 'road' && segment.kind !== 'sea') continue;
    for (const path of splitAtDateline(segment.geometry)) {
      lines.push(new maps.Polyline({ map, path, geodesic: false,
        strokeColor: segment.kind === 'sea' ? '#3865cb' : '#a86a3f', strokeOpacity: 0.82,
        strokeWeight: segment.kind === 'sea' ? 3 : 4 }));
    }
  }
  if (shownPoints.length) map.fitBounds(minimalCircularBounds(shownPoints));
  const marker = new maps.Marker({ map: null, position: { lat: 0, lng: 0 }, label: '', title: 'Illustrative route position' });
  return {
    setProgress(progress: number) {
      const position = routePosition(view, progress);
      if (!position) { marker.setMap(null); return; }
      marker.setPosition(position.point);
      marker.setLabel(position.kind === 'sea' ? '🚢' : '🚚');
      marker.setMap(map);
    },
    dispose() { marker.setMap(null); lines.forEach(line => line.setMap(null)); },
  };
}

type RouteMapState = 'unavailable' | 'loading' | 'ready' | 'failed' | 'auth_failed';

export function RouteMapFallback({ state, retries, onRetry }: {
  state: Exclude<RouteMapState, 'ready'>; retries: number; onRetry: () => void;
}) {
  const message = state === 'loading' ? 'Loading map…'
    : state === 'auth_failed' ? 'Map key authorization failed. The itinerary remains available.'
    : 'Map unavailable · the itinerary remains available.';
  return <div className="zoorq-route-map-fallback" role="status">
    <span aria-hidden="true">◎</span><p>{message}</p>
    {state === 'failed' && retries < 2 && <button type="button" onClick={onRetry}>Retry map</button>}
  </div>;
}

export function ZoorqRouteMap({ view, apiKey = null, reducedMotion, preview = null, onAsk }: ZoorqRouteMapProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<ReturnType<typeof mountRouteMap> | null>(null);
  const [mapState, setMapState] = useState<RouteMapState>(apiKey ? 'loading' : 'unavailable');
  const [playback, dispatchPlayback] = useReducer(routePlaybackReducer, { progress: 0, speed: 1, playing: false });
  const { progress, speed, playing } = playback;
  const contextId = `${view.company_id}|${view.route_id}|${view.material_id}|${view.buyer_location_index}|${apiKey ?? ''}`;
  const [retryState, setRetryState] = useState({ contextId, count: 0 });
  const retries = retryState.contextId === contextId ? retryState.count : 0;
  const [prefersReduced, setPrefersReduced] = useState(prefersRouteReducedMotion);
  const isReduced = reducedMotion ?? prefersReduced;
  const drawable = movingSegments(view);
  const hasMotion = drawable.length > 0;
  const activeSegment = hasMotion ? drawable[Math.min(drawable.length - 1, Math.floor(progress * drawable.length))] : null;
  const primaryLimitation = view.limitations.find(note => note.toLowerCase().includes('selected buyer'))
    ?? view.limitations.find(note => note.toLowerCase().includes('transport mode'))
    ?? view.limitations[0];

  useEffect(() => {
    if (reducedMotion !== undefined || !window.matchMedia) return;
    const query = window.matchMedia('(prefers-reduced-motion: reduce)');
    const update = () => setPrefersReduced(query.matches);
    update();
    query.addEventListener?.('change', update);
    return () => query.removeEventListener?.('change', update);
  }, [reducedMotion]);

  useEffect(() => {
    dispatchPlayback({ type: 'reset' });
    const host = hostRef.current;
    let cancelled = false;
    const onAuthFailure = () => {
      if (cancelled) return;
      mapRef.current?.dispose();
      mapRef.current = null;
      dispatchPlayback({ type: 'pause' });
      setMapState('auth_failed');
    };
    if (!apiKey?.trim() || !host || !hasMotion) { setMapState('unavailable'); return; }
    authFailureListeners.add(onAuthFailure);
    setMapState('loading');
    const lease = acquireRouteMaps(apiKey);
    void lease.promise.then(maps => {
      if (cancelled) return;
      const handle = mountRouteMap(host, view, maps);
      mapRef.current = handle;
      handle.setProgress(0);
      setMapState('ready');
    }).catch(error => {
      if (!cancelled) setMapState(error instanceof Error && error.message.includes('authorization') ? 'auth_failed' : 'failed');
    });
    return () => { cancelled = true; authFailureListeners.delete(onAuthFailure); lease.release(); mapRef.current?.dispose(); mapRef.current = null; };
  }, [apiKey, view, hasMotion, retries]);

  useEffect(() => { mapRef.current?.setProgress(progress); }, [progress]);
  useEffect(() => {
    if (isReduced || !playing || !hasMotion) return;
    let frame = 0;
    let prior = 0;
    const tick = (now: number) => {
      if (prior) dispatchPlayback({ type: 'tick', elapsedMs: Math.min(now - prior, 250) });
      prior = now;
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [playing, speed, isReduced, hasMotion, view]);
  useEffect(() => { if (isReduced) dispatchPlayback({ type: 'pause' }); }, [isReduced]);

  const first = view.itinerary?.origin ?? view.segments.find(segment=>segment.kind==='origin_port')?.label ?? 'Declared origin';
  const last = view.itinerary?.destination ?? view.segments.find(segment=>segment.kind==='buyer')?.label ?? 'Declared destination';
  const declaredStops = view.itinerary ? [view.itinerary.origin,...view.itinerary.via,view.itinerary.destination] :
    [first,...view.segments.filter(segment=>segment.kind==='transport').map(segment=>segment.label),last];
  return <section className="zoorq-route-map" aria-label="Illustrative route itinerary">
    <header className="zoorq-route-map-head">
      <div><span className="zoorq-route-map-kicker">Route simulation · {view.itinerary?.mode === 'sea' ? 'Sea' : view.itinerary?.mode === 'land' ? 'Land' : 'Mode unconfirmed'}</span><h3>{first} → {last}</h3></div>
      <span className="zoorq-route-map-status">{view.status === 'unavailable' ? 'Schematic · Map unavailable' : view.status === 'partial' ? 'Approximate · partial' : 'Approximate'}</span>
    </header>
    {primaryLimitation?.toLowerCase().includes('selected buyer') && <p className="zoorq-route-map-note" role="status">{primaryLimitation}</p>}
    {preview && <div className={`zoorq-route-map-preview is-${preview.kind}`} role="status"><strong>{preview.kind === 'disruption' ? 'Disruption preview' : 'Recovery preview'}</strong><span>{preview.label}</span>{onAsk && <button type="button" onClick={() => onAsk(`Explain ${preview.label} for ${view.company_id} route ${view.route_id}`)}>Ask operator</button>}</div>}
    <div className="zoorq-route-map-layout">
      {hasMotion ? <div className="zoorq-route-map-stage">
        <div className="zoorq-route-map-canvas" ref={hostRef} aria-hidden="true" />
        {mapState !== 'ready' && <RouteMapFallback state={mapState} retries={retries} onRetry={() => setRetryState({ contextId, count: retries + 1 })} />}
        <div className="zoorq-route-map-controls" aria-label="Route simulation controls">
          <button type="button" onClick={() => dispatchPlayback(playing ? { type: 'pause' } : { type: 'play', allowed: !isReduced && hasMotion })} disabled={isReduced || !hasMotion} aria-disabled={isReduced || !hasMotion}>{playing ? 'Pause' : 'Play'}</button>
          <label>Speed <select value={speed} onChange={event => dispatchPlayback({ type: 'speed', speed: Number(event.target.value) })} disabled={isReduced || !hasMotion} aria-label="Simulation speed"><option value={0.5}>0.5×</option><option value={1}>1×</option><option value={2}>2×</option></select></label>
          <label className="zoorq-route-map-scrub"><span className="zoorq-visually-hidden">Simulation progress</span><input type="range" min="0" max="100" value={Math.round(progress * 100)} onChange={event => dispatchPlayback({ type: 'scrub', progress: Number(event.target.value) / 100 })} aria-label="Simulation progress" disabled={!hasMotion} /></label>
        </div>
        {isReduced && <p className="zoorq-route-map-motion">Animation paused for reduced motion; use the progress slider to inspect the corridor.</p>}
      </div> : <div className="zoorq-route-schematic" role="group" aria-label="Declared itinerary schematic"><strong>Declared itinerary</strong><ol aria-label="Declared stops">{declaredStops.map((stop,index)=><li key={`${stop}:${index}`}><span className="zoorq-route-schematic-node" aria-hidden="true"/><div><small>{index===0?'Origin':index===declaredStops.length-1?'Destination':'Declared via'}</small><b>{stop}</b></div></li>)}</ol><p>No mapped path or moving marker is shown. This sequence reflects declared profile stops, without a verified transport path.</p></div>}
      <div className="zoorq-route-map-details"><h3>Along the route</h3>{activeSegment && <p className="zoorq-route-map-current" aria-live="polite">Current leg: {activeSegment.label}</p>}<ol>{view.segments.map(segment => <li key={segment.id} data-kind={segment.kind} aria-current={activeSegment?.id === segment.id ? 'step' : undefined}><span className="zoorq-route-map-dot" aria-hidden="true"/><div><strong>{segment.label}</strong><span className="zoorq-route-leg-status">{segment.status === 'unavailable' ? 'Unavailable' : segment.status === 'approximate' ? 'Approximate' : 'Supported'}</span></div></li>)}</ol><details className="zoorq-route-source"><summary>Source &amp; precision</summary><ul>{view.segments.map(segment=><li key={segment.id}><strong>{segment.label}</strong><span>{segment.precision} · {segment.source}</span></li>)}</ul>{view.limitations.length>0 && <div><strong>Limitations</strong><ul>{view.limitations.map((note,index)=><li key={index}>{note}</li>)}</ul></div>}</details></div>
    </div>
  </section>;
}
