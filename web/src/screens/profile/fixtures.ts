// Unit-test fixtures for the Profile chunk; nothing in the app imports this file.
import { createElement, type ReactElement } from 'react';
import { Route, Routes } from 'react-router-dom';
import type { ProfileState, SectionState } from '../../api/profile';
import type { BookSummary } from '../../api/types';
import { SECTION_ORDER, isFinished, type SectionKey } from '../../lib/profileSections';
import { renderAt, type Entry } from '../../test-utils';
import { LiveRegions } from '../../ui/Live';
import Profile from './Profile';

export function profileState(over: Partial<Record<SectionKey, Partial<SectionState>>> = {}, me: Partial<ProfileState['me']> = {}): ProfileState {
  const minutes: Record<SectionKey, number> = { you: 3, kids: 2, home: 3, benefits: 5, banks: 5 };
  const sections: SectionState[] = SECTION_ORDER.map((key) => ({
    key, status: 'todo', minutes: minutes[key], resume_step: null, by_id: null, by: null, at: null, missing: null, ...over[key],
  }));
  const open = sections.filter((s) => !isFinished(s.status));
  return {
    sections, done: 5 - open.length, total: 5, next: open[0]?.key ?? null, finished: open.length === 0,
    me: { user_id: 1, role: 'jr', seen: true, confirmed: false, later_until: null, ...me },
    names: { jr: 'JR', spouse: 'Alex', joint: 'Joint' },
    people: { jr: { user_id: 1, display_name: 'JR' }, spouse: { user_id: 2, display_name: 'Alex' } },
  };
}

export const FACTS = {
  facts: {
    'jr.dob': { value: '1982-05-01', as_of: '2026-10-01', note: null }, 'spouse.dob': { value: '1984-08-01', as_of: '2026-10-01', note: null },
    state: { value: 'CA', as_of: '2026-10-01', note: null }, filing_status: { value: 'mfj', as_of: '2026-10-01', note: null },
    'jr.salary_cents': { value: '16500000', as_of: '2026-10-01', note: null }, 'jr.k401_pct_bp': { value: '800', as_of: '2026-10-01', note: null },
  },
};
export const ME = { user: { id: 1, username: 'jr', display_name: 'JR', email: 'jr@example.com' }, books: [],
  session: { id: 1, device_label: 'Laptop', created_at: '2026-10-01T00:00:00Z' } };

const routes = (): ReactElement => createElement(Routes, null,
  createElement(Route, { path: '/b/:slug/profile', element: createElement(Profile) }),
  createElement(Route, { path: '/b/:slug/profile/:section', element: createElement(Profile) }),
  createElement(Route, { path: '/b/:slug/profile/:section/:step', element: createElement(Profile) }),
  createElement(Route, { path: '/b/:slug/plan', element: createElement('p', null, 'The Plan') }));

/** The Profile route at `path` (under /b/ours), as routes.tsx mounts it. */
export function atProfile(path: Entry, book: Partial<BookSummary> = {}): ReturnType<typeof renderAt> {
  return renderAt(routes(), book, path);
}

/** atProfile inside the page's live regions (RouteFrame), for a test of what is said: data-testid="live-said". */
export function atProfileLive(path: Entry, book: Partial<BookSummary> = {}): ReturnType<typeof renderAt> {
  return renderAt(createElement(LiveRegions, null, routes()), book, path);
}
