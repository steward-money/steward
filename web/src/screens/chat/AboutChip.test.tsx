import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { setCurrentBook } from '../../api/book';
import type { AboutIn } from '../../ask/about';
import { callsTo, reply, stubFetch } from '../../test-utils';
import { LiveRegions } from '../../ui/Live';
import { AboutChip } from './AboutChip';
import type { AboutReply } from './askApi';
import { useAboutReply, type AboutState } from './useAboutReply';

afterEach(() => { vi.unstubAllGlobals(); setCurrentBook('ours'); });

const REPLY: AboutReply = { about: { kind: 'account', account: 'Account 3' }, sent: "It's about Account 3.", chips: [], labels: { 'Account 3': 'Checking ••6789' } };
const READY: AboutState = { status: 'ready', reply: REPLY, line: null };
const TODAY = new Date(2026, 9, 30);
const chip = (state: AboutState, removed = false, onRemove = vi.fn(), onAddBack = vi.fn()) =>
  render(<AboutChip state={state} removed={removed} onRemove={onRemove} onAddBack={onAddBack} today={TODAY} />);

describe('the chip (P7)', () => {
  it('says what it is about, with the alias muted, and ✕ removes it', () => {
    const onRemove = vi.fn();
    const { container } = chip(READY, false, onRemove);
    expect(container.querySelector('.ask-chip')).toHaveTextContent('About: Checking ••6789 · sent as Account 3');
    expect(container.querySelector('.ask-chip .muted')).toHaveTextContent('· sent as Account 3');
    fireEvent.click(screen.getByRole('button', { name: 'Remove: About Checking ••6789' }));
    expect(onRemove).toHaveBeenCalledOnce();
  });
  it('removed, it stays in its place as a dashed "+ About" that adds it back', () => {
    const onAddBack = vi.fn();
    chip(READY, true, vi.fn(), onAddBack);
    const back = screen.getByRole('button', { name: 'Add back: About Checking ••6789' });
    expect(back).toHaveTextContent('+ About: Checking ••6789');
    expect(back).toHaveClass('ask-chip--removed');
    fireEvent.click(back);
    expect(onAddBack).toHaveBeenCalledOnce();
  });
  it('focus follows to the replacement control, both ways', () => {
    const props = { state: READY, onRemove: vi.fn(), onAddBack: vi.fn(), today: TODAY };
    const { rerender } = render(<AboutChip {...props} removed={false} />);
    screen.getByRole('button', { name: 'Remove: About Checking ••6789' }).focus();
    rerender(<AboutChip {...props} removed />);
    expect(screen.getByRole('button', { name: 'Add back: About Checking ••6789' })).toHaveFocus();
    rerender(<AboutChip {...props} removed={false} />);
    expect(screen.getByRole('button', { name: 'Remove: About Checking ••6789' })).toHaveFocus();
  });
  it("a row that's gone says so in the chip's place", () => {
    render(<LiveRegions><AboutChip state={{ status: 'gone', reply: null, line: "That isn't in this book any more." }} removed={false}
      onRemove={vi.fn()} onAddBack={vi.fn()} today={TODAY} /></LiveRegions>);
    // Issue #51: said through the page's polite region; the line is not a live region that appears with its words.
    expect(screen.getByText("That isn't in this book any more.", { selector: '.ask-gone' })).not.toHaveAttribute('role');
    expect(screen.getByTestId('live-said')).toHaveTextContent("That isn't in this book any more.");
  });
  it('nothing while there is no answer', () => {
    expect(chip({ status: 'loading', reply: null, line: null }).container).toBeEmptyDOMElement();
  });
});

describe('useAboutReply (P8, P28, P31)', () => {
  function run(about: AboutIn | null) {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const spy = vi.spyOn(qc, 'invalidateQueries');
    const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
    return { ...renderHook(({ n }) => useAboutReply(about, n), { initialProps: { n: 0 }, wrapper }), spy };
  }
  it('asks once per nonce, with only the request’s keys', async () => {
    const fetchMock = stubFetch({ 'POST /api/chat/about': REPLY });
    const { result, rerender } = run({ kind: 'account', id: 12 });
    await waitFor(() => expect(result.current.status).toBe('ready'));
    expect(result.current.reply).toEqual(REPLY);
    rerender({ n: 1 });
    await waitFor(() => expect(callsTo(fetchMock, 'POST', '/api/chat/about')).toHaveLength(2));
    expect(callsTo(fetchMock, 'POST', '/api/chat/about')[0].body).toEqual({ about: { kind: 'account', id: 12 } });
  });
  it('a different about drops the old answer until the new one lands', async () => {
    const other: AboutReply = { ...REPLY, labels: { 'Account 3': 'Savings ••1111' } };
    let n = 0;
    stubFetch({ 'POST /api/chat/about': () => reply(++n === 1 ? REPLY : other) });
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
    const { result, rerender } = renderHook(({ a }: { a: AboutIn }) => useAboutReply(a, 0), { initialProps: { a: { kind: 'account', id: 12 } as AboutIn }, wrapper });
    await waitFor(() => expect(result.current.reply).toEqual(REPLY));
    rerender({ a: { kind: 'account', id: 13 } });
    expect(result.current).toEqual({ status: 'loading', reply: null, line: null });
    await waitFor(() => expect(result.current.reply).toEqual(other));
  });
  it("a 404 is the server's gone line", async () => {
    stubFetch({ 'POST /api/chat/about': () => reply({ error: "That isn't in this book any more." }, 404) });
    const { result } = run({ kind: 'transaction', id: 9 });
    await waitFor(() => expect(result.current).toEqual({ status: 'gone', reply: null, line: "That isn't in this book any more." }));
  });
  it('a 409 refreshes the chat status and the book, so the off state shows and the button goes', async () => {
    stubFetch({ 'POST /api/chat/about': () => reply({ error: 'Chat is off for this book. A manager can turn it on in Settings → Chat.' }, 409) });
    const { result, spy } = run({ kind: 'plan' });
    await waitFor(() => expect(result.current.status).toBe('failed'));
    expect(spy).toHaveBeenCalledWith({ queryKey: ['chat', 'status'] });
    expect(spy).toHaveBeenCalledWith({ queryKey: ['me'] });
  });
  it('no about, no request', () => {
    const fetchMock = stubFetch({});
    expect(run(null).result.current.status).toBe('none');
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
