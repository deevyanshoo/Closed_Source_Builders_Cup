import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ZoorqRouteMap, advanceRouteProgress, routePosition, loadRouteMaps, mountRouteMap, prefersRouteReducedMotion, routePlaybackReducer, RouteMapFallback, type RouteDisplayView } from './zoorq-route-map';

const view: RouteDisplayView = {
  schema: 1, company_id: 'solara-cables', route_id: 'solara-cables-route-2', material_id: 'copper',
  buyer_location_index: 1, supplier_id: null, offer_id: null, status: 'partial',
  segments: [
    { id: 'vendor', kind: 'vendor', status: 'unavailable', label: 'Vendor or facility not located', geometry: null, source: 'profile', precision: 'no coordinate' },
    { id: 'origin-port', kind: 'origin_port', status: 'approximate', label: 'Hamburg port area', geometry: [{ lat: 53.54, lng: 9.98 }], source: 'curated', precision: 'unverified approximate port area' },
    { id: 'sea', kind: 'sea', status: 'approximate', label: 'Ship corridor via Suez Canal', geometry: [{ lat: 53.54, lng: 9.98 }, { lat: 30.48, lng: 32.34 }, { lat: 13.1, lng: 80.3 }], source: 'profile via and curated points', precision: 'broad corridor' },
    { id: 'destination-port', kind: 'destination_port', status: 'approximate', label: 'Chennai port area', geometry: [{ lat: 13.1, lng: 80.3 }], source: 'curated', precision: 'unverified approximate port area' },
    { id: 'buyer', kind: 'buyer', status: 'unavailable', label: 'Buyer facility not located', geometry: null, source: 'profile', precision: 'no coordinate' },
  ],
  limitations: ['Supplier, offer, and facility coordinates are unavailable; this is an illustrative profile route.'],
};

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

describe('route display and local playback', () => {
  it('renders a keyboard accessible list, controls and missing detail without Maps', () => {
    const html = renderToStaticMarkup(<ZoorqRouteMap view={view} apiKey={null} />);
    expect(html).toContain('Route simulation');
    expect(html).toContain('Ship corridor via Suez Canal');
    expect(html).toContain('Vendor or facility not located');
    expect(html).toContain('type="range"');
    expect(html).toContain('aria-label="Simulation progress"');
    expect(html).toContain('Play');
    expect(html).toContain('Speed');
    expect(html).toContain('Map unavailable');
    expect(html).toContain('Current leg: Ship corridor via Suez Canal');
    expect(html).toContain('aria-current="step"');
    expect(html).not.toContain('maps.googleapis.com');
  });

  it('shows a read-only disruption preview and supports reduced motion', () => {
    const html = renderToStaticMarkup(<ZoorqRouteMap view={view} reducedMotion preview={{ kind: 'disruption', label: 'Suez delay under review' }} />);
    expect(html).toContain('Suez delay under review');
    expect(html).toContain('Animation paused for reduced motion');
    expect(html).toContain('aria-disabled="true"');
    expect(view.offer_id).toBeNull();
  });

  it('promotes a selected-buyer destination gap above generic limitations', () => {
    const mismatch = { ...view, limitations: [
      'Supplier and facility coordinates are unavailable.',
      'Profile route ends at Mombasa, Kenya; selected buyer is Nairobi, Kenya. The onward connection is unavailable.',
    ] };
    const html = renderToStaticMarkup(<ZoorqRouteMap view={mismatch} apiKey={null} />);
    expect(html).toContain('Profile route ends at Mombasa, Kenya; selected buyer is Nairobi, Kenya');
    expect(html).toContain('Partial corridor');
    expect(html.indexOf('Profile route ends')).toBeLessThan(html.indexOf('zoorq-route-map-layout'));
  });

  it('moves a ship only along a sea segment and wraps local playback', () => {
    expect(routePosition(view, 0)?.kind).toBe('sea');
    expect(routePosition(view, 1)?.point).toEqual({ lat: 13.1, lng: 80.3 });
    expect(advanceRouteProgress(0.9, 1000, 2)).toBeLessThan(1);
    expect(advanceRouteProgress(0.2, 0, 1)).toBe(0.2);
  });

  it('rejects a missing key without creating a script or provider route call', async () => {
    const appendChild = vi.fn();
    vi.stubGlobal('document', { createElement: vi.fn(), head: { appendChild } });
    await expect(loadRouteMaps(null)).rejects.toThrow('browser map key');
    expect(appendChild).not.toHaveBeenCalled();
  });

  it('observes browser reduced motion and handles Maps script failure', async () => {
    const script = { src: '', async: false, defer: false, onerror: null as null | (() => void), remove: vi.fn() };
    const appendChild = vi.fn();
    vi.stubGlobal('window', { matchMedia: () => ({ matches: true }) });
    vi.stubGlobal('document', { createElement: () => script, head: { appendChild } });
    expect(prefersRouteReducedMotion()).toBe(true);
    const loading = loadRouteMaps('synthetic-browser-key');
    expect(appendChild).toHaveBeenCalledWith(script);
    expect(script.src).toContain('maps.googleapis.com/maps/api/js');
    expect(script.src).not.toContain('/directions/');
    script.onerror?.();
    await expect(loading).rejects.toThrow('failed to load');
    expect(script.remove).toHaveBeenCalledOnce();
  });

  it('times out a stalled script and rejects an authorization failure', async () => {
    vi.useFakeTimers();
    const script = { src: '', async: false, defer: false, onerror: null as null | (() => void), remove: vi.fn() };
    vi.stubGlobal('window', {});
    vi.stubGlobal('document', { currentScript: script, createElement: () => script, head: { appendChild: vi.fn() } });
    const stalled = loadRouteMaps('synthetic-browser-key', 10);
    const timeoutResult = expect(stalled).rejects.toThrow('timed out');
    await vi.advanceTimersByTimeAsync(10);
    await timeoutResult;
    const rejected = loadRouteMaps('synthetic-browser-key', 10);
    const authResult = expect(rejected).rejects.toThrow('authorization failed');
    (window as Window & { gm_authFailure?: () => void }).gm_authFailure?.();
    await authResult;
  });

  it('disposes every old overlay on context switch', () => {
    const removed: string[] = [];
    class MapMock { fitBounds() {} }
    class BoundsMock { extend() {} }
    class PolylineMock { setMap(map: unknown) { if (map === null) removed.push('line'); } }
    class MarkerMock { setMap(map: unknown) { if (map === null) removed.push('marker'); } setPosition() {} setLabel() {} }
    const maps = { Map: MapMock, LatLngBounds: BoundsMock, Polyline: PolylineMock, Marker: MarkerMock } as unknown as Parameters<typeof mountRouteMap>[2];
    const first = mountRouteMap({} as HTMLElement, view, maps);
    first.setProgress(0.4);
    first.dispose();
    const second = mountRouteMap({} as HTMLElement, { ...view, company_id: 'terrapack', route_id: 'terrapack-route-1' }, maps);
    second.dispose();
    expect(removed).toEqual(['marker', 'line', 'marker', 'line']);
  });

  it.each([
    { start: 170, end: -170, first: 175, second: -175, pathEnds: [180, -180] },
    { start: -170, end: 170, first: -175, second: 175, pathEnds: [-180, 180] },
  ])('keeps $start to $end marker, line pieces and minimal bounds on the same dateline arc', ({ start, end, first, second, pathEnds }) => {
    const paths: Array<Array<{ lat: number; lng: number }>> = [];
    const fitted: Array<{ west: number; east: number; south: number; north: number }> = [];
    const markers: Array<{ lat: number; lng: number }> = [];
    class MapMock { fitBounds(bounds: { west: number; east: number; south: number; north: number }) { fitted.push(bounds); } }
    class BoundsMock { extend() {} }
    class PolylineMock { constructor(options: { path: Array<{ lat: number; lng: number }> }) { paths.push(options.path); } setMap() {} }
    class MarkerMock { setMap() {} setPosition(point: { lat: number; lng: number }) { markers.push(point); } setLabel() {} }
    const maps = { Map: MapMock, LatLngBounds: BoundsMock, Polyline: PolylineMock, Marker: MarkerMock } as unknown as Parameters<typeof mountRouteMap>[2];
    const crossing = { ...view, segments: [{ ...view.segments[2], geometry: [{ lat: 10, lng: start }, { lat: 10, lng: end }] }] };
    const mounted = mountRouteMap({} as HTMLElement, crossing, maps);
    mounted.setProgress(0.25);
    mounted.setProgress(0.75);
    expect(paths).toHaveLength(2);
    expect(paths[0].at(-1)?.lng).toBe(pathEnds[0]);
    expect(paths[1][0].lng).toBe(pathEnds[1]);
    expect(paths.every(path => path.every((point, index) => !index || Math.abs(point.lng - path[index - 1].lng) <= 180))).toBe(true);
    expect(markers.map(point => point.lng)).toEqual([first, second]);
    expect(fitted[0]).toMatchObject({ west: 170, east: -170, south: 10, north: 10 });
    mounted.dispose();
  });

  it('drives play, pause, speed, scrub and reduced-motion gating through the component controller', () => {
    const initial = { progress: 0, speed: 1, playing: false };
    const playing = routePlaybackReducer(initial, { type: 'play', allowed: true });
    expect(playing.playing).toBe(true);
    const moved = routePlaybackReducer(playing, { type: 'tick', elapsedMs: 1800 });
    expect(moved.progress).toBeCloseTo(0.1);
    const fast = routePlaybackReducer(moved, { type: 'speed', speed: 2 });
    expect(routePlaybackReducer(fast, { type: 'tick', elapsedMs: 1800 }).progress).toBeCloseTo(0.3);
    const scrubbed = routePlaybackReducer(fast, { type: 'scrub', progress: 0.7 });
    const paused = routePlaybackReducer(scrubbed, { type: 'pause' });
    expect(routePlaybackReducer(paused, { type: 'tick', elapsedMs: 1800 }).progress).toBe(0.7);
    expect(routePlaybackReducer(initial, { type: 'play', allowed: false }).playing).toBe(false);
    expect(routePlaybackReducer(playing, { type: 'pause' }).playing).toBe(false);
  });

  it('renders a bounded Retry map action only for recoverable load failure', () => {
    expect(renderToStaticMarkup(<RouteMapFallback state="failed" retries={0} onRetry={() => {}} />)).toContain('Retry map');
    expect(renderToStaticMarkup(<RouteMapFallback state="failed" retries={2} onRetry={() => {}} />)).not.toContain('Retry map');
    expect(renderToStaticMarkup(<RouteMapFallback state="auth_failed" retries={0} onRetry={() => {}} />)).not.toContain('Retry map');
  });

  it('ignores stale completion and authorization callbacks when retrying a script', async () => {
    vi.resetModules();
    const { loadRouteMaps: freshLoad } = await import('./zoorq-route-map');
    const scripts: Array<{ src: string; onerror?: () => void; remove: () => void }> = [];
    const routeWindow: Record<string, unknown> = {};
    vi.stubGlobal('window', routeWindow);
    vi.stubGlobal('document', { createElement: () => {
      const script = { src: '', onerror: undefined as undefined | (() => void), remove: vi.fn() };
      scripts.push(script);
      return script;
    }, head: { appendChild: vi.fn() } });
    const first = freshLoad('synthetic-browser-key');
    const firstResult = expect(first).rejects.toThrow('failed to load');
    const oldCallbackName = new URL(scripts[0].src).searchParams.get('callback')!;
    const oldReady = routeWindow[oldCallbackName] as () => void;
    const oldAuth = routeWindow.gm_authFailure as () => void;
    scripts[0].onerror?.();
    await firstResult;
    const second = freshLoad('synthetic-browser-key');
    let resolved = false;
    void second.then(() => { resolved = true; });
    const newCallbackName = new URL(scripts[1].src).searchParams.get('callback')!;
    expect(newCallbackName).not.toBe(oldCallbackName);
    routeWindow.google = { maps: { Map: class {} } };
    oldReady();
    oldAuth();
    await Promise.resolve();
    expect(resolved).toBe(false);
    (routeWindow[newCallbackName] as () => void)();
    await expect(second).resolves.toBe((routeWindow.google as { maps: unknown }).maps);
  });

  it('releases an unmounted map consumer and cancels a load with no remaining consumers', async () => {
    vi.resetModules();
    const { acquireRouteMaps } = await import('./zoorq-route-map');
    const script = { src: '', remove: vi.fn() };
    vi.stubGlobal('window', {});
    vi.stubGlobal('document', { createElement: () => script, head: { appendChild: vi.fn() } });
    const lease = acquireRouteMaps('synthetic-browser-key');
    const result = expect(lease.promise).rejects.toThrow('cancelled');
    lease.release();
    await result;
    expect(script.remove).toHaveBeenCalledOnce();
  });

  it('allows retry even when script attachment fails synchronously', async () => {
    vi.resetModules();
    const { loadRouteMaps: freshLoad } = await import('./zoorq-route-map');
    let created = 0;
    vi.stubGlobal('window', {});
    vi.stubGlobal('document', {
      createElement: () => { created += 1; return { src: '', remove: vi.fn(), onerror: undefined as undefined | (() => void) }; },
      head: { appendChild: (script: { onerror?: () => void }) => script.onerror?.() },
    });
    await expect(freshLoad('synthetic-browser-key')).rejects.toThrow('failed to load');
    await expect(freshLoad('synthetic-browser-key')).rejects.toThrow('failed to load');
    expect(created).toBe(2);
  });

  it('ignores an explicit old-script invocation of the current global auth hook', async () => {
    vi.resetModules();
    const { loadRouteMaps: freshLoad } = await import('./zoorq-route-map');
    const scripts: Array<{ src: string; onerror?: () => void; remove: () => void }> = [];
    const routeWindow: Record<string, unknown> = {};
    const fakeDocument = { currentScript: null as object | null, createElement: () => {
      const script = { src: '', onerror: undefined as undefined | (() => void), remove: vi.fn() };
      scripts.push(script);
      return script;
    }, head: { appendChild: vi.fn() } };
    vi.stubGlobal('window', routeWindow);
    vi.stubGlobal('document', fakeDocument);
    const first = freshLoad('synthetic-browser-key');
    const firstFailure = expect(first).rejects.toThrow('failed to load');
    scripts[0].onerror?.();
    await firstFailure;
    const second = freshLoad('synthetic-browser-key');
    let settled = false;
    void second.then(() => { settled = true; }, () => { settled = true; });
    fakeDocument.currentScript = scripts[0];
    (routeWindow.gm_authFailure as () => void)();
    await Promise.resolve();
    expect(settled).toBe(false);
    fakeDocument.currentScript = scripts[1];
    routeWindow.google = { maps: { Map: class {} } };
    const callbackName = new URL(scripts[1].src).searchParams.get('callback')!;
    (routeWindow[callbackName] as () => void)();
    await expect(second).resolves.toBe((routeWindow.google as { maps: unknown }).maps);
  });

  it('rejects an asynchronous active auth failure on retry while an old captured handler remains inert', async () => {
    vi.resetModules();
    vi.useFakeTimers();
    const { loadRouteMaps: freshLoad } = await import('./zoorq-route-map');
    const scripts: Array<{ src: string; onerror?: () => void; remove: ReturnType<typeof vi.fn> }> = [];
    const routeWindow: Record<string, unknown> = {};
    vi.stubGlobal('window', routeWindow);
    vi.stubGlobal('document', { currentScript: null, createElement: () => {
      const script = { src: '', onerror: undefined as undefined | (() => void), remove: vi.fn() };
      scripts.push(script);
      return script;
    }, head: { appendChild: vi.fn() } });

    const first = freshLoad('synthetic-browser-key', 10);
    const firstFailure = expect(first).rejects.toThrow('failed to load');
    const oldAuth = routeWindow.gm_authFailure as () => void;
    scripts[0].onerror?.();
    await firstFailure;

    const second = freshLoad('synthetic-browser-key', 10);
    const activeAuth = routeWindow.gm_authFailure as () => void;
    const authFailure = second.then(() => null, error => error as Error);
    let settled = false;
    void second.then(() => { settled = true; }, () => { settled = true; });
    oldAuth();
    await Promise.resolve();
    expect(settled).toBe(false);

    activeAuth(); // Google may invoke this asynchronously with currentScript === null.
    await vi.advanceTimersByTimeAsync(10);
    expect((await authFailure)?.message).toContain('authorization failed');
    expect(scripts[1].remove).toHaveBeenCalledOnce();
    await expect(freshLoad('synthetic-browser-key', 10)).rejects.toThrow('authorization failed');
    expect(scripts).toHaveLength(2);
  });
});
