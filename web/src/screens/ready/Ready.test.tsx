import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { callsTo, reply, stubFetch, wrapBook } from '../../test-utils';
import { LiveRegions } from '../../ui/Live';
import Ready from './Ready';
import type { Ready as R, ReadyRow } from './api';

const fx = vi.hoisted(() => ({ acorn: vi.fn((_o: { motion: string }) => () => {}), winMoment: vi.fn(() => () => {}) }));
vi.mock('../../celebrate/celebrate', () => fx);

beforeEach(() => vi.clearAllMocks());
afterEach(() => vi.unstubAllGlobals());
const row = (key: ReadyRow['key'], status: ReadyRow['status'], detail = '', extra = {}, lines: ReadyRow['lines'] = []): ReadyRow =>
  ({ key, status, detail, go: status === 'done' ? null : 'Check again', lines, extra });
const ADDRESS_LINE = { name: 'STEWARD_BASE_URL', line: 'STEWARD_BASE_URL=https://nas.example.ts.net', running: 'http://nas.lan:8420', note: null, where: 'steward' as const };
const READY = (address: ReadyRow): R => ({ need: address.status === 'needs' ? 1 : 0, rows: [
  row('household', 'done', 'Profile 5 of 5 · Plan 6 of 6'), row('money', 'done'), row('ask', 'optional', 'Not set up.', { url_set: false }),
  row('email', 'optional', 'Not set up.'), row('backups', 'done', 'First copy at 3:00 AM'), row('someone', 'optional', 'None yet.'),
  address, row('key', 'optional')] });

describe('Get Steward ready', () => {
  it('lists the eight rows in order under how many need you, and asks with the page origin', async () => {
    const f = stubFetch({ '/api/ready': READY(row('address', 'needs', 'x', {}, [ADDRESS_LINE])), '/api/install/settings': {} });
    wrapBook(<Ready />, {}, '/b/ours/settings/ready');
    expect(await screen.findByText('1 needs you')).toBeInTheDocument();
    expect(screen.getAllByTestId('ready-row').map((r) => r.querySelector('.setup__title')!.textContent)).toEqual(
      ['Your household', 'Money coming in', 'Ask', 'Email', 'Backups', 'Someone you help', 'Secure address', 'Encryption key']);
    expect(callsTo(f, 'GET', '/api/ready')[0].url).toContain(`origin=${encodeURIComponent(window.location.origin)}`);
  });

  it('Money coming in shows a bank that needs a sign-in, and its panel gives the step and opens Banks (issue 16)', async () => {
    const money = { ...row('money', 'needs', 'Big Bank needs you to sign in again at SimpleFIN.', { linked: true, sign_in: ['Big Bank'] }), go: 'Fix' };
    const ready = READY(row('address', 'done'));
    stubFetch({ '/api/ready': { ...ready, need: 1, rows: ready.rows.map((r) => (r.key === 'money' ? money : r)) }, '/api/install/settings': {} });
    wrapBook(<Ready />, {}, '/b/ours/settings/ready');
    expect(await screen.findByText('1 needs you')).toBeInTheDocument();
    expect(screen.queryByText('Nothing needs you.')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /Money coming in/ }));
    expect(screen.getByText('Sign in again at SimpleFIN Bridge, then choose Update now.')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Banks and accounts' })).toHaveAttribute('href', '/b/ours/settings/banks');
  });

  it('with SimpleFIN unlinked and a sign-in still open, the step says to link it again, as Banks does (issue 16)', async () => {
    const money = { ...row('money', 'needs', 'Big Bank needs you to sign in again at SimpleFIN.', { linked: false, sign_in: ['Big Bank'] }), go: 'Fix' };
    const ready = READY(row('address', 'done'));
    stubFetch({ '/api/ready': { ...ready, need: 1, rows: ready.rows.map((r) => (r.key === 'money' ? money : r)) }, '/api/install/settings': {} });
    wrapBook(<Ready />, {}, '/b/ours/settings/ready');
    fireEvent.click(await screen.findByRole('button', { name: /Money coming in/ }));
    expect(screen.getByText('Sign in again at SimpleFIN Bridge, then link SimpleFIN with a new setup token.')).toBeInTheDocument();
    expect(screen.queryByText(/choose Update now/)).toBeNull();
  });

  it('a boot-level row shows its line, copies only the line, and Check again says what is still running', async () => {
    const write = vi.fn(async () => {});
    Object.defineProperty(navigator, 'clipboard', { value: { writeText: write }, configurable: true });
    stubFetch({ '/api/ready': READY(row('address', 'needs', 'x', {}, [ADDRESS_LINE])), '/api/install/settings': {} });
    wrapBook(<Ready />, {}, '/b/ours/settings/ready');
    fireEvent.click(await screen.findByRole('button', { name: /Secure address/ }));
    expect(screen.getByText('STEWARD_BASE_URL=https://nas.example.ts.net')).toBeInTheDocument();
    expect(screen.getByText("Add this line to Steward's settings file (.env), then restart Steward.")).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole('button', { name: 'Copy' })[0]);
    expect(write).toHaveBeenCalledWith('STEWARD_BASE_URL=https://nas.example.ts.net');
    fireEvent.click(screen.getByRole('button', { name: 'Check again' }));
    const still = 'Not yet. Steward is still running with http://nas.lan:8420. Restart it after saving the file.';
    expect(await screen.findByText(still, { selector: 'p' })).not.toHaveAttribute('role');
    // Issue #51: said through the sheet's polite region, which was there before the line.
    expect(within(screen.getByRole('dialog')).getByTestId('live-said')).toHaveTextContent(still);
  });

  it('with no clipboard (plain http) it never says Copied: the line is selected for copying by hand, and the note says so', async () => {
    Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true });
    stubFetch({ '/api/ready': READY(row('address', 'needs', 'x', {}, [ADDRESS_LINE])), '/api/install/settings': {} });
    wrapBook(<Ready />, {}, '/b/ours/settings/ready');
    fireEvent.click(await screen.findByRole('button', { name: /Secure address/ }));
    fireEvent.click(screen.getAllByRole('button', { name: 'Copy' })[0]);
    expect(await screen.findByText("Couldn't copy, so the line is selected.")).toHaveAttribute('role', 'status');
    expect(screen.queryByText('Copied')).not.toBeInTheDocument();
    expect(window.getSelection()!.toString()).toBe('STEWARD_BASE_URL=https://nas.example.ts.net');
  });

  it('when the restart took, the row turns Done in place', async () => {
    let n = 0;
    stubFetch({ '/api/ready': () => reply(READY(n++ === 0 ? row('address', 'needs', 'x', {}, [ADDRESS_LINE]) : row('address', 'done', 'Steward is at https://nas.example.ts.net.'))),
      '/api/install/settings': {} });
    wrapBook(<Ready />, {}, '/b/ours/settings/ready');
    fireEvent.click(await screen.findByRole('button', { name: /Secure address/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Check again' }));
    expect(await screen.findByText('Took effect.', { selector: 'p' })).toBeInTheDocument();
    expect(screen.getByText('Nothing needs you.')).toBeInTheDocument();
  });

  it('says Nothing needs you with a hop when the last row turns Done while open', async () => {
    let need = 1;
    stubFetch({ '/api/ready': () => reply(READY(need ? row('address', 'needs', 'x', {}, [ADDRESS_LINE]) : row('address', 'done', 'Done.'))),
      '/api/install/settings': {} });
    const r = wrapBook(<LiveRegions><Ready /></LiveRegions>, {}, '/b/ours/settings/ready');
    await screen.findByText('1 needs you');
    expect(fx.acorn).not.toHaveBeenCalled();
    need = 0;
    await act(async () => { await r.qc.invalidateQueries({ queryKey: ['ready'] }); });
    const line = await screen.findByText('Nothing needs you.', { selector: '.ready__cheer' });
    expect(line).not.toHaveAttribute('role');          // said through the page's polite region (issue #51)
    expect(screen.getByTestId('live-said')).toHaveTextContent('Nothing needs you.');
    expect(screen.getAllByText('Nothing needs you.', { selector: 'p' })).toHaveLength(1);
    await waitFor(() => expect(fx.acorn).toHaveBeenCalledWith(expect.objectContaining({ motion: 'hop' })));
    expect(fx.acorn).toHaveBeenCalledTimes(1);
  });

  it('opening the page with nothing needed plays nothing and says nothing', async () => {
    stubFetch({ '/api/ready': () => reply(READY(row('address', 'done', 'Done.'))), '/api/install/settings': {} });
    wrapBook(<LiveRegions><Ready /></LiveRegions>, {}, '/b/ours/settings/ready');
    expect(await screen.findByText('Nothing needs you.')).toBeInTheDocument();
    expect(document.querySelector('.ready__cheer')).toBeNull();
    expect(screen.getByTestId('live-said').textContent).toBe('');    // already true when it opened (review of #51)
    expect(fx.acorn).not.toHaveBeenCalled();
  });
});

describe('Get Steward ready, the Secure address row and passkeys (INS-22)', () => {
  const DONE = row('address', 'done', 'Steward is at https://steward.lan.');
  let get: ReturnType<typeof vi.fn>;
  const signals: AbortSignal[] = [];
  beforeEach(() => {
    signals.length = 0;
    vi.stubGlobal('PublicKeyCredential', function PublicKeyCredential() {});
  });
  afterEach(() => { Reflect.deleteProperty(navigator, 'credentials'); });
  const browser = (answer: (signal: AbortSignal) => Promise<unknown>) => {
    get = vi.fn((o: CredentialRequestOptions) => { signals.push(o.signal!); return answer(o.signal!); });
    Object.defineProperty(navigator, 'credentials', { value: { get }, configurable: true });
  };
  const addressRow = () => screen.getAllByTestId('ready-row').find((r) => r.textContent?.includes('Secure address'))!;

  it('past a certificate warning, the browser refuses passkeys: the row needs you and says how to fix it', async () => {
    browser(() => Promise.reject(new DOMException('WebAuthn is not supported on sites with TLS certificate errors.', 'NotAllowedError')));
    stubFetch({ '/api/ready': READY(DONE), '/api/install/settings': {} });
    wrapBook(<Ready />, {}, '/b/ours/settings/ready');
    expect(await screen.findByText('1 needs you')).toBeInTheDocument();
    expect(addressRow()).toHaveTextContent("This browser doesn't trust the certificate here, so passkeys won't work.");
    expect(addressRow()).toHaveTextContent('Needs you');
    expect(get).toHaveBeenCalledWith(expect.objectContaining({ mediation: 'conditional' }));     // no prompt on the page
    fireEvent.click(screen.getByRole('button', { name: /Secure address/ }));
    expect(screen.getByText(/Trust Caddy's root certificate on each phone and computer\./)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'How' })).toHaveAttribute('href', expect.stringContaining('access.md#lan-only'));
  });

  it('at an IP address the browser refuses passkeys too, and the row says why', async () => {
    browser(() => Promise.reject(new DOMException('This is an invalid domain.', 'SecurityError')));
    stubFetch({ '/api/ready': READY(DONE), '/api/install/settings': {} });
    wrapBook(<Ready />, {}, '/b/ours/settings/ready');
    expect(await screen.findByText("Passkeys don't work at an IP address.")).toBeInTheDocument();
    expect(screen.getByText('1 needs you')).toBeInTheDocument();
  });

  it('a trusted address leaves the quiet request waiting: Done stays, and leaving the page aborts it', async () => {
    browser((signal) => new Promise((_, reject) => signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))));
    stubFetch({ '/api/ready': READY(DONE), '/api/install/settings': {} });
    const r = wrapBook(<Ready />, {}, '/b/ours/settings/ready');
    expect(await screen.findByText('Nothing needs you.')).toBeInTheDocument();
    await waitFor(() => expect(get).toHaveBeenCalledTimes(1));
    expect(addressRow()).toHaveTextContent('Steward is at https://steward.lan.');
    expect(addressRow()).toHaveTextContent('Done');
    r.unmount();
    expect(signals[0].aborted).toBe(true);
  });

  it('asks the browser nothing while the server already says the address needs you', async () => {
    browser(() => Promise.reject(new DOMException('x', 'NotAllowedError')));
    stubFetch({ '/api/ready': READY(row('address', 'needs', 'x', {}, [ADDRESS_LINE])), '/api/install/settings': {} });
    wrapBook(<Ready />, {}, '/b/ours/settings/ready');
    expect(await screen.findByText('1 needs you')).toBeInTheDocument();
    expect(get).not.toHaveBeenCalled();
  });
});
