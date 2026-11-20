import { fireEvent, screen, within } from '@testing-library/react';
import { Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { BookSummary } from '../api/types';
import { celebrationsChosen, forgetPersonPrefs, setPersonPrefs, soundOn } from '../lib/celebrations';
import { crossedMilestone, firstMaxed, joyScope, MAXED_KEY, MILESTONE_KEY } from '../screens/accounts/joy';
import { EMPTY_DRAFT, withAssumed } from '../screens/plan/levers';
import { getSavedNote, setSavedNote } from '../screens/plan/savedNote';
import { clearKeptDraft, draftKey, keepDraft, keptDraft } from '../screens/plan/usePlanDraft';
import { callsTo, stubFetch, wrapBook } from '../test-utils';
import { YouMenu, youRows } from './YouMenu';

afterEach(() => vi.unstubAllGlobals());
const OURS: BookSummary = { slug: 'ours', name: 'Ours', kind: 'household', role: 'manager', available: true, reason: null, owners: [], profile: null };
const ME = { user: { id: 1, username: 'jr', display_name: 'JR Hale', email: 'jr@example.com' },
  session: { id: 7, device_label: 'Laptop', created_at: '2026-10-01T00:00:00Z' },
  books: [OURS] };

describe('the You menu (decision 4)', () => {
  it('opens from the initials with the name, the email, Settings for the book, Sign-in and devices, Appearance, Help and Sign out', async () => {
    stubFetch({ '/api/auth/me': ME });
    wrapBook(<YouMenu variant="bar" />, ME.books[0], '/b/ours');
    fireEvent.click(await screen.findByRole('button', { name: 'You: JR Hale' }));
    const menu = screen.getByRole('dialog', { name: 'JR Hale' });
    expect(within(menu).getByText('jr@example.com')).toBeInTheDocument();
    expect(within(menu).getAllByRole('link').map((a) => [a.textContent, a.getAttribute('href')])).toEqual([
      ['Profile', '/b/ours/profile'], ['Settings for Ours', '/b/ours/settings'], ['Sign-in and devices', '/b/ours/you/devices'], ['Appearance', '/b/ours/you/appearance'], ['Help', '/b/ours/you/help'], ['About Steward', '/b/ours/you/about']]);
    expect(within(menu).getByRole('button', { name: 'Sign out' })).toBeInTheDocument();
  });
  it('Sign out asks first, then signs out and goes to the sign-in page', async () => {
    const fetchMock = stubFetch({ '/api/auth/me': ME, 'POST /api/auth/logout': () => new Response(null, { status: 204 }) });
    wrapBook(<Routes><Route path="/login" element={<p>Sign in</p>} /><Route path="*" element={<YouMenu variant="bar" />} /></Routes>, ME.books[0], '/b/ours');
    fireEvent.click(await screen.findByRole('button', { name: 'You: JR Hale' }));
    fireEvent.click(screen.getByRole('button', { name: 'Sign out' }));
    fireEvent.click(within(screen.getByRole('dialog', { name: 'Sign out of this device?' })).getByRole('button', { name: 'Sign out' }));
    expect(await screen.findByText('Sign in')).toBeInTheDocument();
    expect(callsTo(fetchMock, 'POST', '/api/auth/logout')).toHaveLength(1);
  });
  it('signing out forgets the unsaved plan moves and the saved note, so the next person on this tab sees neither (F-SEC)', async () => {
    stubFetch({ '/api/auth/me': ME, 'POST /api/auth/logout': () => new Response(null, { status: 204 }) });
    const moved = withAssumed(EMPTY_DRAFT, 'save_more_monthly_cents', 25000);
    keepDraft(draftKey(1, 7), moved);
    setSavedNote({ planId: 7, at: '2026-10-28T12:00:00Z', day: '2026-10-28', by: 'JR', previous: null });
    try {
      wrapBook(<Routes><Route path="/login" element={<p>Sign in</p>} /><Route path="*" element={<YouMenu variant="bar" />} /></Routes>, ME.books[0], '/b/ours');
      fireEvent.click(await screen.findByRole('button', { name: 'You: JR Hale' }));
      expect(keptDraft(draftKey(1, 7))).toEqual(moved);
      fireEvent.click(screen.getByRole('button', { name: 'Sign out' }));
      fireEvent.click(within(screen.getByRole('dialog', { name: 'Sign out of this device?' })).getByRole('button', { name: 'Sign out' }));
      expect(await screen.findByText('Sign in')).toBeInTheDocument();
      expect(keptDraft(draftKey(1, 7))).toBe(EMPTY_DRAFT);
      expect(getSavedNote(7)).toBeNull();
    } finally {
      clearKeptDraft();
    }
  });
  it("signing out forgets this person's Celebrations and Sound, so the next person on this tab gets their own (phase 17, 3.6)", async () => {
    stubFetch({ '/api/auth/me': ME, 'POST /api/auth/logout': () => new Response(null, { status: 204 }) });
    setPersonPrefs({ celebrations: false, sound: true });
    try {
      wrapBook(<Routes><Route path="/login" element={<p>Sign in</p>} /><Route path="*" element={<YouMenu variant="bar" />} /></Routes>, ME.books[0], '/b/ours');
      fireEvent.click(await screen.findByRole('button', { name: 'You: JR Hale' }));
      expect(celebrationsChosen()).toBe(false);
      fireEvent.click(screen.getByRole('button', { name: 'Sign out' }));
      fireEvent.click(within(screen.getByRole('dialog', { name: 'Sign out of this device?' })).getByRole('button', { name: 'Sign out' }));
      expect(await screen.findByText('Sign in')).toBeInTheDocument();
      expect(celebrationsChosen()).toBe(true);
      expect(soundOn()).toBe(false);
    } finally {
      forgetPersonPrefs();
    }
  });
  it("signing out removes the net worth milestone and maxed cheers this device kept, and leaves the device's settings (issue #6)", async () => {
    stubFetch({ '/api/auth/me': ME, 'POST /api/auth/logout': () => new Response(null, { status: 204 }) });
    crossedMilestone(joyScope(1, 'ours'), 95_000_000);
    firstMaxed(joyScope(1, 'ours'), 'jr:k401:2026');
    localStorage.setItem(MILESTONE_KEY, '90000000');                  // what 0.1.0 kept, unscoped
    localStorage.setItem('steward-swipe', 'off');
    wrapBook(<Routes><Route path="/login" element={<p>Sign in</p>} /><Route path="*" element={<YouMenu variant="bar" />} /></Routes>, ME.books[0], '/b/ours');
    fireEvent.click(await screen.findByRole('button', { name: 'You: JR Hale' }));
    expect(Object.keys(localStorage).filter((k) => k.startsWith(MILESTONE_KEY) || k.startsWith(MAXED_KEY))).toHaveLength(3);
    fireEvent.click(screen.getByRole('button', { name: 'Sign out' }));
    fireEvent.click(within(screen.getByRole('dialog', { name: 'Sign out of this device?' })).getByRole('button', { name: 'Sign out' }));
    expect(await screen.findByText('Sign in')).toBeInTheDocument();
    expect(Object.keys(localStorage).filter((k) => k.startsWith(MILESTONE_KEY) || k.startsWith(MAXED_KEY))).toEqual([]);
    expect(localStorage.getItem('steward-swipe')).toBe('off');
  });
  it('a household viewer has no Settings row', async () => {
    const viewer = { ...ME, books: [{ ...ME.books[0], role: 'viewer' as const }] };
    stubFetch({ '/api/auth/me': viewer });
    wrapBook(<YouMenu variant="bar" />, viewer.books[0], '/b/ours');
    fireEvent.click(await screen.findByRole('button', { name: 'You: JR Hale' }));
    expect(screen.queryByRole('link', { name: /Settings for/ })).toBeNull();
  });
  it('a parent book has no Profile row', async () => {
    const parentBook: BookSummary = { ...OURS, kind: 'parent' };
    stubFetch({ '/api/auth/me': { ...ME, books: [parentBook] } });
    wrapBook(<YouMenu variant="bar" />, parentBook, '/b/ours');
    fireEvent.click(await screen.findByRole('button', { name: 'You: JR Hale' }));
    expect(screen.queryByRole('link', { name: 'Profile' })).toBeNull();
  });
  it('on the laptop rail it shows the name too and opens above the button; Escape closes it, giving focus back', async () => {
    stubFetch({ '/api/auth/me': ME });
    wrapBook(<YouMenu variant="rail" />, ME.books[0], '/b/ours');
    const button = await screen.findByRole('button', { name: 'You: JR Hale' });
    expect(button).toHaveTextContent('JR Hale');
    fireEvent.click(button);
    expect(button).toHaveAttribute('aria-expanded', 'true');
    expect(within(screen.getByRole('dialog', { name: 'JR Hale' })).getByRole('link', { name: 'Appearance' })).toBeInTheDocument();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(document.activeElement).toBe(button);
  });
  it('on the rail, cancelling Sign out gives focus back to the initials', async () => {
    stubFetch({ '/api/auth/me': ME });
    wrapBook(<YouMenu variant="rail" />, ME.books[0], '/b/ours');
    const button = await screen.findByRole('button', { name: 'You: JR Hale' });
    fireEvent.click(button);
    const signOut = within(screen.getByRole('dialog', { name: 'JR Hale' })).getByRole('button', { name: 'Sign out' });
    signOut.focus();
    fireEvent.click(signOut);
    fireEvent.click(within(screen.getByRole('dialog', { name: 'Sign out of this device?' })).getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(document.activeElement).toBe(button);
  });
  it('a name of only spaces shows the username', async () => {
    stubFetch({ '/api/auth/me': { ...ME, user: { ...ME.user, display_name: '  ' } } });
    wrapBook(<YouMenu variant="rail" />, ME.books[0], '/b/ours');
    const button = await screen.findByRole('button', { name: 'You: jr' });
    expect(button.querySelector('.you__initials')).toHaveAttribute('data-initials', 'JR');
  });
});

describe('youRows with Help (guided setup spec 7.5)', () => {
  it('lists Help before Sign out, linking to /you/help', () => {
    const rows = youRows(OURS, true);
    expect(rows.map((r) => r.key)).toEqual(['profile', 'settings', 'devices', 'appearance', 'help', 'about', 'sign-out']);
    expect(rows.find((r) => r.key === 'help')).toMatchObject({ label: 'Help', to: '/you/help' });
  });
  it('offers About Steward to everyone, before Sign out', () => {
    const parent = { ...OURS, kind: 'parent' } as BookSummary;
    for (const rows of [youRows(parent, false), youRows(OURS, true)]) {
      expect(rows.at(-2)).toMatchObject({ key: 'about', label: 'About Steward', to: '/you/about' });
    }
  });
});
