import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, ApiError, isAbort, NETWORK_MESSAGE } from './client';
import { setCurrentBook } from './book';

afterEach(() => vi.unstubAllGlobals());

const ok = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });

describe('api()', () => {
  it('sends JSON with a JSON type, and keeps a type the caller chose', async () => {
    const f = vi.fn(async () => ok({ fine: true }));
    vi.stubGlobal('fetch', f);
    await api('/a', { method: 'POST', body: JSON.stringify({ x: 1 }) });
    await api('/b', { method: 'POST', body: new Blob(['<x/>'], { type: 'text/xml' }), headers: { 'Content-Type': 'application/xml' } });
    const headers = (i: number) => (f.mock.calls[i] as unknown as [string, RequestInit])[1].headers as Record<string, string>;
    expect(headers(0)['Content-Type']).toBe('application/json');
    expect(headers(1)['Content-Type']).toBe('application/xml');
    expect(Object.keys(headers(1)).filter((k) => k.toLowerCase() === 'content-type')).toHaveLength(1);
  });

  it('carries the error body, so a 503 keeps last_saved', async () => {
    const last = { comfortable_age: 63, likely_age: 60, saved_at: '2026-10-12T19:00:00+00:00', saved_day: '2026-10-12' };
    vi.stubGlobal('fetch', vi.fn(async () => ok({ error: "The plan couldn't be worked out right now.", last_saved: last }, 503)));
    const e = await api<never>('/api/plan').catch((x: ApiError) => x);
    expect(e).toBeInstanceOf(ApiError);
    expect(e.status).toBe(503);
    expect(e.message).toBe("The plan couldn't be worked out right now.");
    expect((e.body as { last_saved: unknown }).last_saved).toEqual(last);
  });

  it('rethrows an abort instead of calling it a network failure', async () => {
    const ctrl = new AbortController();
    vi.stubGlobal('fetch', vi.fn(async () => { throw new DOMException('The operation was aborted.', 'AbortError'); }));
    ctrl.abort();
    const e = await api('/api/plan/preview', { method: 'POST', body: '{}', signal: ctrl.signal }).catch((x) => x);
    expect(isAbort(e)).toBe(true);
    expect(e).not.toBeInstanceOf(ApiError);
  });

  it('still reports a real network failure in plain words', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('Failed to fetch'); }));
    const e = await api<never>('/api/summary').catch((x: ApiError) => x);
    expect(e).toBeInstanceOf(ApiError);
    expect(e.message).toBe(NETWORK_MESSAGE);
    // THO-15: the app's own words for the server, and advice that fits a LAN install and a Tailscale one.
    expect(e.message).toBe("Steward couldn't reach your home server. Check that this device is on your home network or Tailscale.");
  });

  it('sends book routes to the open book and person routes to the top level', async () => {
    const fn = vi.fn(async (_path: string) => new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } }));
    vi.stubGlobal('fetch', fn);
    setCurrentBook('mom');
    await api('/api/accounts');
    await api('/api/auth/me');
    expect(fn.mock.calls.map((c) => c[0])).toEqual(['/api/b/mom/accounts', '/api/auth/me']);
    setCurrentBook('ours');
  });
});
