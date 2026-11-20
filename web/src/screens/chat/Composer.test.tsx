import { act, fireEvent, screen, within } from '@testing-library/react';
import { useState, type ComponentProps } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useFakeTimers } from '../../test-clock';
import { setCurrentBook } from '../../api/book';
import { reply, stubFetch, wrapBook } from '../../test-utils';
import { LiveRegions } from '../../ui/Live';
import { chipWhy, Composer } from './Composer';

beforeEach(() => useFakeTimers());
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); setCurrentBook('ours'); });

const BRACES = "Can Alex and I pay for Maya's braces at Bright Smile Ortho, $6,400, from Checking 6789?";
const BRACES_SENT = "Can Person 2 and I pay for Child 1's braces at [organization], $6,400, from Account 1?";
const BRACES_CHIPS = [
  { start: 4, end: 12, label: 'Person 2', layer: 'steward' }, { start: 27, end: 34, label: 'Child 1', layer: 'steward' },
  { start: 47, end: 61, label: '[organization]', layer: 'gliner' }, { start: 76, end: 85, label: 'Account 1', layer: 'steward' }];
const LABELS = { 'Person 2': 'Alex', 'Child 1': 'Maya', 'Account 1': 'Checking ••6789' };

function Host({ onSend, ...more }: { onSend: (t: string) => void } & Partial<ComponentProps<typeof Composer>>) {
  const [v, setV] = useState('');
  return <Composer value={v} onChange={setV} onSend={onSend} disabled={false} placeholder="Ask about this book’s money" {...more} />;
}

async function type(text: string) {
  fireEvent.change(screen.getByLabelText('Your question'), { target: { value: text } });
  await act(async () => { await vi.advanceTimersByTimeAsync(450); });
}

describe('Composer', () => {
  it('shows What Claude will see 400 ms after typing stops, with each kind of chip', async () => {
    const fetchMock = stubFetch({ 'POST /api/chat/preview': { text: BRACES_SENT, as_written: false, chips: BRACES_CHIPS, labels: LABELS } });
    wrapBook(<Host onSend={vi.fn()} />);
    fireEvent.change(screen.getByLabelText('Your question'), { target: { value: BRACES } });
    await act(async () => { await vi.advanceTimersByTimeAsync(300); });
    expect(fetchMock).not.toHaveBeenCalled();
    await act(async () => { await vi.advanceTimersByTimeAsync(150); });
    expect(await screen.findByRole('button', { name: /Person 2/ })).toHaveClass('tok');
    expect(screen.getByRole('button', { name: /\[organization\]/ })).toHaveClass('tok--model');
    expect(screen.getByText('Replaced in Steward')).toBeInTheDocument();
    expect(screen.getByText("Removed by the gateway's check")).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Person 2/ }));
    expect(screen.getByText('Replaced in Steward. Alex is in this book, so Claude gets "Person 2". Answers show "Alex" again.')).toBeInTheDocument();
  });

  // Review of #51: the preview appears with its words, so it is said through the page's region, not as a live region.
  it('says what Claude will see through the page region when the preview appears', async () => {
    stubFetch({ 'POST /api/chat/preview': { text: 'Can we cover a year on one income?', as_written: true, chips: [], labels: {} } });
    wrapBook(<LiveRegions><Host onSend={vi.fn()} /></LiveRegions>);
    await type('Can we cover a year on one income?');
    const line = await screen.findByText('Claude will see your question as written.', { selector: '.willsend *' });
    expect(line.closest('.willsend')).not.toHaveAttribute('aria-live');
    expect(screen.getByTestId('live-said')).toHaveTextContent('Claude will see your question as written.');
  });

  it('says the question goes as written when nothing was replaced', async () => {
    stubFetch({ 'POST /api/chat/preview': { text: 'Can we cover a year on one income?', as_written: true, chips: [], labels: {} } });
    wrapBook(<Host onSend={vi.fn()} />);
    await type('Can we cover a year on one income?');
    expect(await screen.findByText('Claude will see your question as written.')).toBeInTheDocument();
  });

  it('sends at once when only Steward replaced parts', async () => {
    stubFetch({ 'POST /api/chat/preview': { text: 'Is Person 2 paid up?', as_written: false, chips: [{ start: 3, end: 11, label: 'Person 2', layer: 'steward' }], labels: { 'Person 2': 'Alex' } } });
    const onSend = vi.fn();
    wrapBook(<Host onSend={onSend} />);
    await type('Is Alex paid up?');
    await screen.findByRole('button', { name: /Person 2/ });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    expect(onSend).toHaveBeenCalledWith('Is Alex paid up?');
  });

  it('shows the What Claude will see card first when the model check removed something, and Edit goes back', async () => {
    stubFetch({ 'POST /api/chat/preview': { text: BRACES_SENT, as_written: false, chips: BRACES_CHIPS, labels: LABELS } });
    const onSend = vi.fn();
    wrapBook(<Host onSend={onSend} />);
    await type(BRACES);
    await screen.findByRole('button', { name: /\[organization\]/ });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    expect(onSend).not.toHaveBeenCalled();
    const card = screen.getByRole('group', { name: 'What Claude will see' });
    expect(card).toHaveTextContent("The gateway's check took out 1 part it guessed was a name or a number.");
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    expect(screen.queryByRole('group', { name: 'What Claude will see' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    fireEvent.click(screen.getAllByRole('button', { name: 'Send' })[0]);
    expect(onSend).toHaveBeenCalledWith(BRACES);
  });

  it('a failed preview disables Send and says why', async () => {
    stubFetch({ 'POST /api/chat/preview': () => reply({ error: "Chat needs the gateway, which isn't answering. It may be asleep or turned off." }, 503) });
    wrapBook(<Host onSend={vi.fn()} />);
    await type('How much is in savings?');
    expect(await screen.findByText(/What Claude will see can't be shown, so nothing can be sent\. Chat needs the gateway/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled();
  });

  it('explains a model chip without quoting it, and a Steward bracket by what it matched', () => {
    expect(chipWhy({ start: 0, end: 14, label: '[organization]', layer: 'gliner' }, {})).toEqual([
      "Removed by the gateway's check. It looked like the name of a business, so it isn't sent.",
      "Claude can still answer from the rest. If it isn't needed, nothing changes; if it's wrong, edit the question."]);
    expect(chipWhy({ start: 0, end: 4, label: '[name]', layer: 'gliner' }, {})[0]).toBe(
      "Removed by the gateway's check. It looked like a person's name, so it isn't sent. The check looks for names Steward doesn't hold and can miss one.");
    expect(chipWhy({ start: 0, end: 7, label: '[email]', layer: 'steward' }, {})).toEqual([
      "Replaced in Steward. It matched an email address this book knows, so it isn't sent."]);
    expect(chipWhy({ start: 0, end: 9, label: 'Account 1', layer: 'steward' }, { 'Account 1': 'Checking ••6789' })).toEqual([
      'Replaced in Steward. This is Checking ••6789 in this book, so Claude gets "Account 1". Account names and numbers aren\'t sent.']);
  });

  it('explains a PIN, password or code as not sent, from either layer', () => {
    expect(chipWhy({ start: 0, end: 8, label: '[secret]', layer: 'steward' }, {})).toEqual([
      "Replaced in Steward. It came right after a PIN, password or code, so it isn't sent."]);
    expect(chipWhy({ start: 0, end: 8, label: '[secret]', layer: 'presidio' }, {})[0]).toBe(
      "Removed by the gateway's check. It looked like a PIN, password or code, so it isn't sent.");
  });

  it('does not send on a stale preview when the newest text fails to preview', async () => {
    let n = 0;
    stubFetch({ 'POST /api/chat/preview': () => (++n === 1
      ? reply({ text: 'How much is in savings?', as_written: true, chips: [], labels: {} })
      : reply({ error: "Chat needs the gateway, which isn't answering. It may be asleep or turned off." }, 503)) });
    const onSend = vi.fn();
    wrapBook(<Host onSend={onSend} />);
    await type('How much is in savings?');
    await screen.findByText('Claude will see your question as written.');
    fireEvent.change(screen.getByLabelText('Your question'), { target: { value: 'How much is in checking?' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await act(async () => { await vi.advanceTimersByTimeAsync(450); });
    expect(await screen.findByText(/What Claude will see can't be shown/)).toBeInTheDocument();
    expect(onSend).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled();
  });

  it('text typed after tapping Send cancels the wait: it never goes out on its own when its preview lands', async () => {
    stubFetch({ 'POST /api/chat/preview': { text: 'How much is in checking?', as_written: true, chips: [], labels: {} } });
    const onSend = vi.fn();
    wrapBook(<Host onSend={onSend} />);
    fireEvent.change(screen.getByLabelText('Your question'), { target: { value: 'How much is in savings?' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));          // no preview yet: Send waits for it
    fireEvent.change(screen.getByLabelText('Your question'), { target: { value: 'How much is in checking?' } });
    await act(async () => { await vi.advanceTimersByTimeAsync(450); });
    expect(await screen.findByText('Claude will see your question as written.')).toBeInTheDocument();
    expect(onSend).not.toHaveBeenCalled();
  });
});

const ABOUT_LINE = { sent: "It's about Account 3.", chips: [{ start: 11, end: 20, label: 'Account 3', layer: 'alias' }], labels: { 'Account 3': 'Checking ••6789' } };

describe('Composer with an about line (Ask about this)', () => {
  it('shows the about line first, before anything is typed, with its alias as a chip; the question follows under it', async () => {
    stubFetch({ 'POST /api/chat/preview': { text: 'Is this higher than usual?', as_written: true, chips: [], labels: {} } });
    wrapBook(<Host onSend={vi.fn()} about={ABOUT_LINE} />);
    const will = document.querySelector('.willsend') as HTMLElement;
    expect(will).toHaveTextContent("It's about Account 3, replaced in Steward.");   // the chip says its layer to a screen reader
    fireEvent.click(within(will).getByRole('button', { name: /Account 3/ }));
    expect(screen.getByText('Replaced in Steward. This is Checking ••6789 in this book, so Claude gets "Account 3". Account names and numbers aren\'t sent.')).toBeInTheDocument();
    expect(screen.getByText('Tap a replaced part to see why.')).toBeInTheDocument();
    await type('Is this higher than usual?');
    expect(await within(will).findByText('Claude will see your question as written.')).toBeInTheDocument();
    expect(will.textContent!.indexOf("It's about")).toBeLessThan(will.textContent!.indexOf('Claude will see your question'));
  });

  it('quiet (the panel): no composer note, but the tap note when there are chips', () => {
    const first = wrapBook(<Host onSend={vi.fn()} quiet />);
    expect(screen.queryByText(/As you type, this shows/)).toBeNull();
    first.unmount();
    wrapBook(<Host onSend={vi.fn()} quiet about={ABOUT_LINE} />);
    expect(screen.getByText('Tap a replaced part to see why.')).toBeInTheDocument();
  });

  it('the first-use tip: the box points to it, the label replays it, Got it ends it', () => {
    const onToggle = vi.fn();
    const onDone = vi.fn();
    wrapBook(<Host onSend={vi.fn()} about={ABOUT_LINE} tour={{ show: true, example: false, onToggle, onDone }} />);
    const tip = screen.getByTestId('ask-tour');
    expect(tip).toHaveTextContent('Names, stores and accounts turn into placeholders before anything leaves Steward. Tap one to see what it stands for.');
    expect(screen.getByLabelText('Your question')).toHaveAttribute('aria-describedby', tip.id);
    expect(document.querySelector('.willsend')).toHaveClass('is-tour');
    const label = screen.getByRole('button', { name: 'What Claude will see' });
    expect(label).toHaveAttribute('aria-expanded', 'true');
    fireEvent.click(label);
    expect(onToggle).toHaveBeenCalledOnce();
    fireEvent.click(within(tip).getByRole('button', { name: 'Got it' }));
    expect(onDone).toHaveBeenCalledOnce();
  });

  it('with no live preview (The gateway away) the tip carries the fixed example', () => {
    wrapBook(<Host onSend={vi.fn()} about={ABOUT_LINE} disabled tour={{ show: true, example: true, onToggle: vi.fn(), onDone: vi.fn() }} />);
    const tip = screen.getByTestId('ask-tour');
    expect(within(tip).getByText('Example')).toBeInTheDocument();
    expect(within(tip).getByText('How much did Sam spend at Costco on our Chase card?')).toBeInTheDocument();
    fireEvent.click(within(tip).getByRole('button', { name: /Payee 4/ }));
    expect(within(tip).getByText('Replaced in Steward. This is Costco, so Claude gets "Payee 4". Store and payee names aren\'t sent unless a manager approves them.')).toBeInTheDocument();
  });

  it('each box has its own id', () => {
    wrapBook(<><Host onSend={vi.fn()} /><Host onSend={vi.fn()} /></>);
    const [a, b] = screen.getAllByLabelText('Your question');
    expect(a.id).not.toBe(b.id);
  });
});
