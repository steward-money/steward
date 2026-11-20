import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { useState, type ReactElement } from 'react';
import { Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { setCurrentBook } from '../../../api/book';
import type { BookSummary } from '../../../api/types';
import { AskHost } from '../../../ask/AskHost';
import { askState, resetAsk } from '../../../ask/store';
import { resetTourLoad } from '../../../lib/askTour';
import { letTestingLibraryDriveFakeTimers, useFakeTimers } from '../../../test-clock';
import { callsTo, renderAt, reply, stubFetch } from '../../../test-utils';
import { AskAbout } from '../../../ui/AskAbout';
import { PREVIEW_DELAY_MS } from '../Composer';
import { resetTourHidden } from '../useAskTour';
import { AWAY, CHECKING_IN, CHECKING_REPLY, CONV7, DINING_IN, DINING_REPLY, DINING_SURFACE, DONE, held, ME, ndjson, STARTED, STATUS } from './fixtures';

beforeEach(() => {
  useFakeTimers();
  letTestingLibraryDriveFakeTimers();
  localStorage.clear();
  localStorage.setItem('steward-ask-tour:1', 'done');        // the tip has its own tests below
  resetTourLoad();
  resetTourHidden();
});
afterEach(() => { resetAsk(); vi.unstubAllGlobals(); vi.useRealTimers(); setCurrentBook('ours'); });

function Page() {
  return (
    <main>
      <h1>Eating out · October 2026</h1>
      <AskAbout about={DINING_IN} surface={DINING_SURFACE} thing="Eating out" />
      <AskAbout about={CHECKING_IN} surface={{ kind: 'account', group: 'everyday' }} thing="Checking ••6789" />
      <AskHost />
    </main>
  );
}
const preview = (_u: string, init: RequestInit) => reply({ text: JSON.parse(String(init.body)).text, as_written: true, chips: [], labels: {} });
function mount(routes: Record<string, unknown> = {}, ui: ReactElement = <Page />, book: Partial<BookSummary> = {}) {
  const fetchMock = stubFetch({
    '/api/chat': STATUS, '/api/auth/me': ME, '/api/chat/conversations/7': CONV7, 'POST /api/chat/prewarm': { sent: false },
    'POST /api/chat/preview': preview,
    'POST /api/chat/about': (_u: string, init: RequestInit) => reply(JSON.parse(String(init.body)).about.kind === 'account' ? CHECKING_REPLY : DINING_REPLY),
    ...routes,
  });
  const r = renderAt(<Routes><Route path="/b/:slug/ask/*" element={<p>The Ask tab</p>} /><Route path="/b/:slug/*" element={ui} /></Routes>,
    { chat: true, ...book }, '/spending/dining');
  return { fetchMock, ...r };
}
const open = (name = 'Ask about Eating out') => { fireEvent.click(screen.getByRole('button', { name })); };
const panel = () => screen.getByRole('dialog', { name: 'Ask' });
const closed = () => waitFor(() => expect(screen.queryByRole('dialog', { name: 'Ask' })).toBeNull());
const box = () => within(panel()).getByRole('textbox', { name: 'Your question' });
const escape = () => { fireEvent.keyDown(document.body, { key: 'Escape' }); };
async function ask(text: string) {
  await waitFor(() => expect(box()).not.toBeDisabled());
  fireEvent.change(box(), { target: { value: text } });
  await act(async () => { await vi.advanceTimersByTimeAsync(PREVIEW_DELAY_MS); });
  fireEvent.click(within(panel()).getByRole('button', { name: 'Send' }));
}
const chipText = () => panel().querySelector('.ask-chip')?.textContent ?? '';

describe('the panel (P2, P27)', () => {
  it('opens on the page with focus in the box; Escape closes it and focus goes back to the button', async () => {
    mount();
    const button = screen.getByRole('button', { name: 'Ask about Eating out' });
    open();
    await waitFor(() => expect(document.activeElement).toBe(box()));
    expect(screen.getByRole('heading', { level: 1, name: 'Eating out · October 2026' })).toBeInTheDocument();
    escape();
    await closed();
    expect(button).toHaveFocus();
  });

  it("when the button that opened it is gone, focus goes to the page's heading", async () => {
    function Card() {
      const [here, setHere] = useState(true);
      return (
        <main>
          <h1>Review</h1>
          {here && <AskAbout about={{ kind: 'transaction', id: 1 }} surface={{ kind: 'transaction', category: 'uncategorized' }} thing="SHOP 1" />}
          <button type="button" onClick={() => { setHere(false); }}>Sorted elsewhere</button>
          <AskHost />
        </main>
      );
    }
    mount({}, <Card />);
    open('Ask about SHOP 1');
    await waitFor(() => expect(document.activeElement).toBe(box()));
    fireEvent.click(screen.getByRole('button', { name: 'Sorted elsewhere' }));
    escape();
    await closed();
    expect(screen.getByRole('heading', { level: 1, name: 'Review' })).toHaveFocus();
  });

  it('switching books drops it', async () => {
    const { unmount } = mount();
    open();
    await waitFor(() => expect(box()).toBeInTheDocument());
    unmount();
    expect(askState().mounted).toBe(false);
  });
});

describe('the chip (P7, P28)', () => {
  it('says what it is about; ✕ leaves "+ About" in its place and the question goes without it; add back restores it', async () => {
    const { fetchMock } = mount({ 'POST /api/chat/ask': () => ndjson(STARTED, DONE('Yes.')) });
    open('Ask about Checking ••6789');
    await waitFor(() => expect(chipText()).toBe('About: Checking ••6789 · sent as Account 3'));
    fireEvent.click(within(panel()).getByRole('button', { name: 'Remove: About Checking ••6789' }));
    const back = within(panel()).getByRole('button', { name: 'Add back: About Checking ••6789' });
    expect(back).toHaveTextContent('+ About: Checking ••6789');
    await ask('Why?');
    await waitFor(() => expect(callsTo(fetchMock, 'POST', '/api/chat/ask')).toHaveLength(1));
    expect(callsTo(fetchMock, 'POST', '/api/chat/ask')[0].body).toEqual({ question: 'Why?', conversation_id: null });
    fireEvent.click(back);
    await waitFor(() => expect(chipText()).toBe('About: Checking ••6789 · sent as Account 3'));
    expect(callsTo(fetchMock, 'POST', '/api/chat/about')).toHaveLength(2);        // once on open, again on add-back
  });

  it("a row that's gone says so in the chip's place, and the question still goes", async () => {
    const { fetchMock } = mount({ 'POST /api/chat/about': () => reply({ error: "That isn't in this book any more." }, 404),
      'POST /api/chat/ask': () => ndjson(STARTED, DONE('From totals.')) });
    open();
    expect(await within(panel()).findByText("That isn't in this book any more.", { selector: '.ask-gone' })).toBeInTheDocument();
    await ask('Why?');
    await waitFor(() => expect(callsTo(fetchMock, 'POST', '/api/chat/ask')[0]?.body).toEqual({ question: 'Why?', conversation_id: null }));
  });

  it('a row merged away since the chip: the question is refused with the line, and asking again without the chip works', async () => {
    let n = 0;
    const { fetchMock } = mount({ 'POST /api/chat/ask': () => (++n === 1 ? reply({ error: "That isn't in this book any more." }, 404) : ndjson(STARTED, DONE('From totals.'))) });
    open();
    await waitFor(() => expect(chipText()).toBe('About: Eating out · October'));
    await ask('Why?');
    expect(await within(panel()).findByText("That isn't in this book any more.")).toBeInTheDocument();
    fireEvent.click(within(panel()).getByRole('button', { name: 'Remove: About Eating out · October' }));
    await ask('Why?');
    await waitFor(() => expect(callsTo(fetchMock, 'POST', '/api/chat/ask')).toHaveLength(2));
    expect(callsTo(fetchMock, 'POST', '/api/chat/ask')[1].body).toEqual({ question: 'Why?', conversation_id: null });
    expect(await within(panel()).findByText('From totals.')).toBeInTheDocument();
  });
});

describe('What Claude will see and the suggestions (P4, decision 4)', () => {
  it('shows the about line before anything is typed, then the question under it', async () => {
    mount();
    open('Ask about Checking ••6789');
    await waitFor(() => expect(panel().querySelector('.willsend')).toHaveTextContent("It's about Account 3, replaced in Steward."));
    const will = panel().querySelector('.willsend') as HTMLElement;
    expect(within(will).getByRole('button', { name: /Account 3/ })).toHaveClass('tok');
    fireEvent.change(box(), { target: { value: 'Is this higher than usual?' } });
    await act(async () => { await vi.advanceTimersByTimeAsync(PREVIEW_DELAY_MS); });
    expect(await within(will).findByText('Claude will see your question as written.')).toBeInTheDocument();
    expect(within(panel()).getByText('Tap a replaced part to see why.')).toBeInTheDocument();
    expect(within(panel()).queryByText(/As you type, this shows/)).toBeNull();
    expect(box()).toHaveAttribute('placeholder', '');
  });

  it('three suggestions fit the page; tapping one fills the box and sends nothing', async () => {
    const { fetchMock } = mount();
    open();
    await waitFor(() => expect(panel().querySelectorAll('.ask-sugg .example')).toHaveLength(3));
    const buttons = [...panel().querySelectorAll<HTMLButtonElement>('.ask-sugg .example')];
    expect(buttons.map((b) => b.textContent)).toEqual(['What made up Eating out in October?', 'Is this higher than usual?', 'Which places did we go to most?']);
    fireEvent.click(buttons[1]);
    expect(box()).toHaveValue('Is this higher than usual?');
    expect(callsTo(fetchMock, 'POST', '/api/chat/ask')).toHaveLength(0);
  });
});

describe('asking in the panel (P3, P24)', () => {
  it('Send posts the about; a follow-up carries it and the conversation; Open in Ask carries both, the draft never in the address', async () => {
    const stream = held();
    let n = 0;
    const { fetchMock, location } = mount({ 'POST /api/chat/ask': () => (++n === 1 ? stream.response() : ndjson({ ...STARTED, turn_id: 't_2' }, DONE('Still yes.'))) });
    open();
    await ask('Is this higher than usual?');
    await waitFor(() => expect(callsTo(fetchMock, 'POST', '/api/chat/ask')).toHaveLength(1));
    expect(callsTo(fetchMock, 'POST', '/api/chat/ask')[0].body).toEqual({ question: 'Is this higher than usual?', conversation_id: null, about: DINING_IN });
    await act(async () => { stream.push(STARTED); });
    expect(within(panel()).queryByRole('button', { name: 'Open in Ask' })).toBeNull();     // hidden while it arrives
    await act(async () => { stream.push(DONE('Yes, $120 more.')); stream.end(); });
    await waitFor(() => expect(panel()).toHaveTextContent('Yes, $120 more.'));           // the $ amount is set in its own span
    expect(box()).toHaveAttribute('placeholder', 'Ask a follow-up');
    await ask('Why?');
    await waitFor(() => expect(callsTo(fetchMock, 'POST', '/api/chat/ask')).toHaveLength(2));
    expect(callsTo(fetchMock, 'POST', '/api/chat/ask')[1].body).toEqual({ question: 'Why?', conversation_id: 7, about: DINING_IN });
    expect(await within(panel()).findByText('Still yes.')).toBeInTheDocument();
    fireEvent.change(box(), { target: { value: 'A draft that names Sam' } });
    fireEvent.click(within(panel()).getByRole('button', { name: 'Open in Ask' }));
    await waitFor(() => expect(location.current.pathname).toBe('/b/ours/ask/c/7'));
    expect(location.current.search).toBe('?about=category:dining:2026-10');
    expect(location.current.state).toEqual({ draft: 'A draft that names Sam' });
    await closed();                                                         // a route change closes it
  });

  it('closing while an answer arrives keeps it coming; when it is done, "Answer ready · Open" reopens it', async () => {
    const stream = held();
    const { fetchMock } = mount({ 'POST /api/chat/ask': () => stream.response() });
    open();
    await ask('Is this higher than usual?');
    await act(async () => { stream.push(STARTED); });
    fireEvent.click(within(panel()).getByRole('button', { name: 'Close' }));
    await closed();
    await act(async () => { stream.push(DONE('Yes, $120 more.')); stream.end(); });
    const toast = (await screen.findByText('Answer ready')).closest('.undo-toast') as HTMLElement;
    expect(toast).toHaveTextContent('Answer ready · Open');
    expect(callsTo(fetchMock, 'POST', '/api/chat/turns')).toHaveLength(0);                 // never stopped
    fireEvent.click(within(toast).getByRole('button', { name: 'Open' }));
    await waitFor(() => expect(panel()).toHaveTextContent('Yes, $120 more.'));
    expect(document.querySelector('.undo-toast')).toBeNull();
  });

  it('opening from the same thing resumes; from something else starts again, unless an answer is still arriving', async () => {
    const stream = held();
    mount({ 'POST /api/chat/ask': () => stream.response() });
    open();
    await waitFor(() => expect(document.activeElement).toBe(box()));
    fireEvent.change(box(), { target: { value: 'Half a thought' } });
    escape();
    await closed();
    open();
    await waitFor(() => expect(box()).toHaveValue('Half a thought'));                     // the same thing resumes
    await ask('Half a thought');
    await act(async () => { stream.push(STARTED); });
    escape();
    await closed();
    open('Ask about Checking ••6789');                    // while an answer is arriving, Ask on something else resumes it
    await waitFor(() => expect(chipText()).toBe('About: Eating out · October'));
    await act(async () => { stream.push(DONE('Yes.')); stream.end(); });
    expect(await within(panel()).findByText('Yes.')).toBeInTheDocument();
    escape();
    await closed();
    open('Ask about Checking ••6789');                    // once it's done, something else starts again
    await waitFor(() => expect(chipText()).toBe('About: Checking ••6789 · sent as Account 3'));
    expect(within(panel()).queryByText('Yes.')).toBeNull();
    expect(box()).toHaveValue('');
  });
});

describe('the states (States table)', () => {
  it('chat turned off elsewhere: /about answers 409, the panel shows the off state and the book is fetched again', async () => {
    let on = true;
    const { fetchMock } = mount({ '/api/chat': () => reply({ ...STATUS, on }),
      'POST /api/chat/about': () => { on = false; return reply({ error: STATUS.off_line }, 409); } });
    open();
    expect(await within(panel()).findByText('Chat is off for this book')).toBeInTheDocument();
    await waitFor(() => expect(callsTo(fetchMock, 'GET', '/api/auth/me').length).toBeGreaterThan(1));
  });

  it('not seen: Before you ask, and Not now closes the panel', async () => {
    mount({ '/api/chat': { ...STATUS, seen: false } });
    open();
    fireEvent.click(await within(panel()).findByRole('button', { name: 'Not now' }));
    await closed();
    expect(screen.getByRole('heading', { level: 1, name: 'Eating out · October 2026' })).toBeInTheDocument();
  });

  it('The gateway away: the chip and its line, the away state, the box paused', async () => {
    mount({ '/api/chat': AWAY });
    open();
    expect(await within(panel()).findByText('Chat needs the gateway')).toBeInTheDocument();
    await waitFor(() => expect(chipText()).toBe('About: Eating out · October'));
    expect(panel().querySelector('.willsend')).toHaveTextContent("It's about Eating out (category dining) in October 2026.");
    expect(box()).toBeDisabled();
    expect(box()).toHaveAttribute('placeholder', 'Paused until the gateway answers');
  });

  it('budget used up: the cap state and the box closed until next month', async () => {
    mount({ '/api/chat': { ...STATUS, budget: { ...STATUS.budget, cap_line: "This month's budget is used up." } } });
    open();
    expect(await within(panel()).findByText("This month's budget is used up")).toBeInTheDocument();
    expect(within(panel()).getByRole('link', { name: 'Change the budget' })).toBeInTheDocument();
    expect(box()).toBeDisabled();
    expect(box()).toHaveAttribute('placeholder', 'Chat starts again on Nov 1');
  });
});

describe('the first-use tip (P23)', () => {
  beforeEach(() => { localStorage.clear(); });

  it('shows once per person: Got it marks it done for this person only', async () => {
    mount();
    open();
    const tip = await within(panel()).findByTestId('ask-tour');
    expect(tip).toHaveTextContent('Names, stores and accounts turn into placeholders before anything leaves Steward.');
    expect(box()).toHaveAttribute('aria-describedby', tip.id);
    expect(panel().querySelector('.willsend')).toHaveClass('is-tour');
    fireEvent.click(within(tip).getByRole('button', { name: 'Got it' }));
    expect(localStorage.getItem('steward-ask-tour:1')).toBe('done');
    expect(localStorage.getItem('steward-ask-tour:2')).toBeNull();
    expect(within(panel()).queryByTestId('ask-tour')).toBeNull();
  });

  it('another person on this device still sees it', async () => {
    localStorage.setItem('steward-ask-tour:1', 'done');
    mount({ '/api/auth/me': { ...ME, user: { ...ME.user, id: 2 } } });
    open();
    expect(await within(panel()).findByTestId('ask-tour')).toBeInTheDocument();
  });

  it('sending a question marks it done', async () => {
    mount({ 'POST /api/chat/ask': () => ndjson(STARTED, DONE('Yes.')) });
    open();
    await within(panel()).findByTestId('ask-tour');
    await ask('Why?');
    await waitFor(() => expect(localStorage.getItem('steward-ask-tour:1')).toBe('done'));
  });

  it('the label replays it without touching storage, and Escape closes the tip before the panel', async () => {
    localStorage.setItem('steward-ask-tour:1', 'done');
    mount();
    open();
    const label = await within(panel()).findByRole('button', { name: 'What Claude will see' });
    expect(label).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(label);
    expect(within(panel()).getByTestId('ask-tour')).toBeInTheDocument();
    expect(label).toHaveAttribute('aria-expanded', 'true');
    escape();
    expect(within(panel()).queryByTestId('ask-tour')).toBeNull();
    expect(panel()).toBeInTheDocument();
    escape();
    await closed();
    expect(localStorage.getItem('steward-ask-tour:1')).toBe('done');
  });

  it('while the gateway is away it shows the fixed example', async () => {
    mount({ '/api/chat': AWAY });
    open();
    const tip = await within(panel()).findByTestId('ask-tour');
    expect(within(tip).getByText('Example')).toBeInTheDocument();
    expect(within(tip).getByText('How much did Sam spend at Costco on our Chase card?')).toBeInTheDocument();
    expect(within(tip).getByRole('button', { name: /Payee 4/ })).toHaveClass('tok');
  });
});

describe('a Sample', () => {
  it('shows the one line and calls no chat route: not the status, the about, nor prewarm', async () => {
    const { fetchMock } = mount({}, <Page />, { sample: true, chat: true });
    open();
    expect(await within(panel()).findByText('Ask is off in the Sample.')).toBeInTheDocument();
    expect(fetchMock.mock.calls.filter(([u]) => /\/api\/(b\/[^/]+\/)?chat/.test(String(u)))).toEqual([]);
  });
});
