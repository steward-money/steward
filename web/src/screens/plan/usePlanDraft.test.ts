import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook } from '@testing-library/react';
import { createElement, type ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useFakeTimers } from '../../test-clock';
import { setCurrentBook } from '../../api/book';
import type { Me } from '../../api/types';
import { DEBOUNCE_MS } from '../../lib/useDebouncedRequest';
import { callsTo, reply, stubFetch } from '../../test-utils';
import type { PlanReady } from './api';
import { impactsFixture, planFixture } from './fixtures';
import { EMPTY_DRAFT, withAssumed } from './levers';
import { getSavedNote, setSavedNote } from './savedNote';
import { clearKeptDraft, draftKey, keepDraft, keptDraft, usePlanDraft } from './usePlanDraft';

beforeEach(() => { useFakeTimers(); });
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); clearKeptDraft(); setCurrentBook(null); });

const tick = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });

/** The hook as a screen runs it: inside a query client where `userId` is signed in (the ['me'] query, from the cache). */
function renderDraft(data: PlanReady, userId = 1) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  qc.setQueryData(['me'], { user: { id: userId, username: `u${userId}`, display_name: '', email: null }, books: [],
    session: { id: 1, device_label: 'Laptop', created_at: '2026-10-01T00:00:00Z' } } as unknown as Me);
  const wrapper = ({ children }: { children: ReactNode }) => createElement(QueryClientProvider, { client: qc }, children);
  return renderHook(() => usePlanDraft(data), { wrapper });
}

describe('usePlanDraft: impacts after a preview', () => {
  it('waits for a pause after the ages land, so a continuing drag never sends them', async () => {
    const data = planFixture();
    const fetchMock = stubFetch({
      'POST /api/plan/preview': () => reply({ levers: data.levers, result: { ...data.result, comfortable_age: 62 } }),
      'POST /api/plan/impacts': () => reply({ levers: data.levers, impacts: impactsFixture() }),
    });
    const moved = () => callsTo(fetchMock, 'POST', '/api/plan/impacts').filter((c) => (c.body as { assumed?: unknown }).assumed);
    const { result } = renderDraft(data);
    await tick(DEBOUNCE_MS);

    act(() => result.current.setDraft((d) => withAssumed(d, 'save_more_monthly_cents', 25000)));
    await tick(DEBOUNCE_MS);
    expect(result.current.result.comfortable_age).toBe(62);
    await tick(DEBOUNCE_MS - 10);
    expect(moved()).toHaveLength(0);

    // The drag goes on: the next value aborts the first impacts before it is sent.
    act(() => result.current.setDraft((d) => withAssumed(d, 'save_more_monthly_cents', 30000)));
    await tick(DEBOUNCE_MS);
    await tick(DEBOUNCE_MS);
    expect(moved().map((c) => c.body)).toEqual([{ assumed: { save_more_monthly_cents: 30000 } }]);
  });
});

describe('usePlanDraft: unsaved moves outlive a trip to another Plan page (plan subpages, Review Focus 1)', () => {
  it('keeps the draft for the same saved plan, and drops it once another plan is saved', () => {
    const data = planFixture();
    stubFetch({
      'POST /api/plan/preview': () => reply({ levers: data.levers, result: { ...data.result, comfortable_age: 62 } }),
      'POST /api/plan/impacts': () => reply({ levers: data.levers, impacts: impactsFixture() }),
    });
    const first = renderDraft(data);
    act(() => first.result.current.setDraft((d) => withAssumed(d, 'save_more_monthly_cents', 25000)));
    first.unmount();
    const again = renderDraft(data);
    expect(again.result.current.draft.assumed).toEqual({ save_more_monthly_cents: 25000 });
    expect(again.result.current.dirty).toBe(true);
    again.unmount();
    const other = planFixture();
    other.plan = { ...other.plan!, id: 9 };
    const third = renderDraft(other);
    expect(third.result.current.dirty).toBe(false);
  });
});

describe('usePlanDraft: kept moves stay with their book and person (plan subpages, F-SEC)', () => {
  const moved = withAssumed(EMPTY_DRAFT, 'save_more_monthly_cents', 25000);

  it('a draft kept for plan 7 in one book reads as empty for plan 7 in another book', () => {
    setCurrentBook('ours');
    keepDraft(draftKey(1, 7), moved);
    expect(keptDraft(draftKey(1, 7))).toEqual(moved);
    setCurrentBook('theirs');
    expect(keptDraft(draftKey(1, 7))).toBe(EMPTY_DRAFT);
    setCurrentBook('ours');
    expect(keptDraft(draftKey(1, 7))).toEqual(moved);
  });

  it('another person, or another book, opening the same plan id starts from the saved plan', () => {
    const data = planFixture();
    stubFetch({
      'POST /api/plan/preview': () => reply({ levers: data.levers, result: { ...data.result, comfortable_age: 62 } }),
      'POST /api/plan/impacts': () => reply({ levers: data.levers, impacts: impactsFixture() }),
    });
    const moveIn = (book: string) => {
      setCurrentBook(book);
      const mine = renderDraft(data, 1);
      act(() => mine.result.current.setDraft((d) => withAssumed(d, 'save_more_monthly_cents', 25000)));
      mine.unmount();
    };
    moveIn('ours');
    const someoneElse = renderDraft(data, 2);
    expect(someoneElse.result.current.dirty).toBe(false);
    someoneElse.unmount();
    moveIn('ours');
    setCurrentBook('theirs');
    const otherBook = renderDraft(data, 1);
    expect(otherBook.result.current.dirty).toBe(false);
  });

  it('clearKeptDraft() forgets the kept moves and the saved note', () => {
    keepDraft(draftKey(1, 7), moved);
    setSavedNote({ planId: 7, at: '2026-10-28T12:00:00Z', day: '2026-10-28', by: 'JR', previous: null });
    clearKeptDraft();
    expect(keptDraft(draftKey(1, 7))).toBe(EMPTY_DRAFT);
    expect(getSavedNote(7)).toBeNull();
  });
});
