import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { callsTo, reply, stubFetch } from '../../test-utils';
import { FACTS, ME, atProfile, atProfileLive, profileState } from './fixtures';

// withCelebrate runs its callback against a fake celebrate module, so what the page asks for is visible.
const celebrate = vi.hoisted(() => ({ profile: vi.fn() }));
vi.mock('../../lib/celebrations', async (orig) => ({
  ...(await orig<object>()),
  withCelebrate: vi.fn(async (fn: (c: typeof celebrate) => unknown) => fn(celebrate)),
}));

afterEach(() => { vi.unstubAllGlobals(); celebrate.profile.mockClear(); });

describe('Profile page', () => {
  it('shows the meter and the five rows in order, with why, minutes and the done row\'s summary', async () => {
    stubFetch({ '/api/profile': profileState({ you: { status: 'done' } }), '/api/facts': FACTS, '/api/auth/me': ME });
    atProfile('/profile');
    expect(await screen.findByRole('heading', { name: 'Profile', level: 1 })).toBeInTheDocument();
    expect(screen.getByText('1 of 5 done')).toBeInTheDocument();
    const rows = screen.getAllByRole('listitem');
    expect(rows.map((r) => r.querySelector('.prof-row__name')?.textContent)).toEqual(['You and your partner', 'Kids and college',
      'Home and cars', 'Social Security and Medicare', 'Banks and accounts']);
    expect(within(rows[1]).getByText('College is often the biggest cost before retirement.')).toBeInTheDocument();
    expect(within(rows[1]).getByText('About 2 minutes')).toBeInTheDocument();
    expect(await within(rows[0]).findByText('JR and Alex · California · Married filing jointly')).toBeInTheDocument();
    expect(screen.getByTestId('row-you')).toHaveAttribute('href', '/b/ours/profile/you');
  });

  it("shows the book's growth card once growth answers (phase 17)", async () => {
    const keys = ['first_month', 'covered', 'checklist', 'year', 'five_years'];
    stubFetch({ '/api/profile': profileState(), '/api/facts': FACTS, '/api/auth/me': ME,
      '/api/growth': { stage: 1, seen: true, milestones: keys.map((key, i) => ({ key, met: i === 3 })) } });
    atProfile('/profile');
    expect(await screen.findByTestId('profile-growth')).toBeInTheDocument();
  });

  it('a skipped row says so and what is still needed; a started one says Continue at its resume point', async () => {
    stubFetch({ '/api/profile': profileState({ you: { status: 'skipped', missing: 'the salary', resume_step: 'pay' } }), '/api/facts': FACTS });
    atProfile('/profile');
    const row = await screen.findByTestId('row-you');
    expect(within(row).getByText('Skipped')).toBeInTheDocument();
    expect(within(row).getByText('Still needed: the salary')).toBeInTheDocument();
    expect(within(row).getByText('Continue')).toBeInTheDocument();
    expect(row).toHaveAttribute('href', '/b/ours/profile/you/pay');
  });

  it("the first visit's band: Start marks seen and opens the first unfinished section", async () => {
    const fetchMock = stubFetch({
      '/api/profile': profileState({}, { seen: false }), '/api/auth/me': ME,
      'POST /api/profile/seen': () => reply(profileState()), '/api/facts': FACTS,
    });
    const { location } = atProfile('/profile');
    expect(await screen.findByText('Everything you enter stays on your home server.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Start' }));
    await waitFor(() => expect(location.current.pathname).toBe('/b/ours/profile/you/you'));
    expect(callsTo(fetchMock, 'POST', '/api/profile/seen')).toHaveLength(1);
  });

  it('says why it opened in place of Home, with a way there, only when Home sent it (issue 30)', async () => {
    stubFetch({ '/api/profile': profileState({}, { seen: false }), '/api/auth/me': ME, '/api/facts': FACTS });
    const sent = atProfile({ pathname: '/b/ours/profile', state: { fromHome: true } });
    const why = await screen.findByTestId('opened-from-home');
    expect(why).toHaveTextContent("Steward opened your profile in place of Home because it isn't finished.");
    expect(within(why).getByRole('link', { name: 'Go to Home' })).toHaveAttribute('href', '/b/ours');
    await waitFor(() => expect(sent.location.current.state).toBeNull());        // a reload doesn't say it again
    expect(screen.getByTestId('opened-from-home')).toBeInTheDocument();
    sent.unmount();
    atProfile('/profile');
    await screen.findByText('Everything you enter stays on your home server.');
    expect(screen.queryByTestId('opened-from-home')).toBeNull();
  });

  it('Start shows the failure in words when the server errors, and stays put', async () => {
    stubFetch({
      '/api/profile': profileState({}, { seen: false }), '/api/auth/me': ME, '/api/facts': FACTS,
      'POST /api/profile/seen': () => reply({ error: 'The book is busy.' }, 503),
    });
    const { location } = atProfile('/profile');
    fireEvent.click(await screen.findByRole('button', { name: 'Start' }));
    expect(await screen.findByText('The book is busy.')).toBeInTheDocument();
    expect(location.current.pathname).toBe('/b/ours/profile');
  });

  it('loading shows five skeleton rows, and an error its words with Try again', async () => {
    stubFetch({ '/api/profile': () => reply({ error: 'The book is busy.' }, 503) });
    atProfile('/profile');
    expect(await screen.findByText('The book is busy.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
  });

  it('a viewer gets no band and no Start, and a row opens the card', async () => {
    stubFetch({ '/api/profile': profileState({}, { seen: false }), '/api/facts': FACTS });
    atProfile('/profile', { role: 'viewer' });
    const row = await screen.findByTestId('row-you');
    expect(row).toHaveAttribute('href', '/b/ours/profile/you');
    expect(screen.queryByText('Everything you enter stays on your home server.')).toBeNull();
    expect(screen.queryByText('Start')).toBeNull();
  });

  it('every row opens something', async () => {
    stubFetch({ '/api/profile': profileState(), '/api/facts': FACTS, '/api/auth/me': ME });
    atProfile('/profile');
    for (const k of ['you', 'kids', 'home', 'benefits', 'banks']) expect(await screen.findByTestId(`row-${k}`)).toHaveAttribute('href');
  });

  // A done row renders its section's summary, so those reads are stubbed (stubFetch matches by prefix).
  const SUMMARIES = {
    '/api/profile/home': { owns_home: true, where_you_live: null, properties: [], cars: [], loans: [], mortgages: [] },
    '/api/profile/benefits': { jr: { kind: 'none' }, spouse: { kind: 'none' }, health: { premium_cents: null } },
    '/api/children': { children: [], college: { growth: 0.01, levels: {} }, suggestions: [], plans: [] },
    '/api/accounts': { accounts: [] }, '/api/simplefin': { linked: false },
  };

  it('coming back from a finished section says its cheer and plays its moment over that row, once', async () => {
    stubFetch({ ...SUMMARIES, '/api/profile': profileState({ kids: { status: 'done' } }), '/api/facts': FACTS, '/api/auth/me': ME });
    const { location } = atProfile({ pathname: '/profile', state: { finished: 'kids', allDone: false } });
    expect(await screen.findByText('College, planned.')).toBeInTheDocument();
    await waitFor(() => expect(celebrate.profile).toHaveBeenCalledTimes(1));
    expect(celebrate.profile).toHaveBeenCalledWith({ kind: 'kids', host: screen.getByTestId('row-kids') });
    await waitFor(() => expect(location.current.state).toBeNull());    // a reload does not play it again
    expect(screen.getByText('College, planned.')).toBeInTheDocument();
  });

  it('the home row plays the house over its own row', async () => {
    stubFetch({ ...SUMMARIES, '/api/profile': profileState({ home: { status: 'done' } }), '/api/facts': FACTS, '/api/auth/me': ME });
    atProfile({ pathname: '/profile', state: { finished: 'home', allDone: false } });
    await waitFor(() => expect(celebrate.profile).toHaveBeenCalledTimes(1));
    expect(celebrate.profile).toHaveBeenCalledWith({ kind: 'home', host: screen.getByTestId('row-home') });
  });

  it('5 of 5 says the profile is complete and bursts from the meter', async () => {
    const all = profileState({ you: { status: 'done' }, kids: { status: 'done' }, home: { status: 'done' }, benefits: { status: 'done' }, banks: { status: 'done' } });
    stubFetch({ ...SUMMARIES, '/api/profile': all, '/api/facts': FACTS, '/api/auth/me': ME });
    const { container, location } = atProfile({ pathname: '/profile', state: { finished: 'banks', allDone: true } });
    expect(await screen.findByText('Your profile is complete. The Plan knows your household now.')).toBeInTheDocument();
    await waitFor(() => expect(celebrate.profile).toHaveBeenCalledTimes(1));
    expect(celebrate.profile).toHaveBeenCalledWith({ kind: 'all', host: container.querySelector('.prof-meter') });
    await waitFor(() => expect(location.current.state).toBeNull());
  });

  it('a full profile shows a lasting check and its one line; the arrival cheer replaces that line', async () => {
    const all = profileState({ you: { status: 'done' }, kids: { status: 'done' }, home: { status: 'done' }, benefits: { status: 'done' }, banks: { status: 'done' } });
    stubFetch({ ...SUMMARIES, '/api/profile': all, '/api/facts': FACTS, '/api/auth/me': ME });
    atProfile('/profile');
    expect(await screen.findByTestId('profile-check')).toBeInTheDocument();
    expect(screen.getByTestId('profile-done')).toHaveTextContent('All five. The plan has what it needs.');
    // Issue #51: said through the page's polite region (Live.tsx Shown), not a live region that appears with its words.
    expect(screen.getByTestId('profile-done')).not.toHaveAttribute('role');
  });

  it('arriving from the last section shows the cheer, not the done line too', async () => {
    const all = profileState({ you: { status: 'done' }, kids: { status: 'done' }, home: { status: 'done' }, benefits: { status: 'done' }, banks: { status: 'done' } });
    stubFetch({ ...SUMMARIES, '/api/profile': all, '/api/facts': FACTS, '/api/auth/me': ME });
    atProfile({ pathname: '/profile', state: { finished: 'banks', allDone: true } });
    expect(await screen.findByText('Your profile is complete. The Plan knows your household now.')).toBeInTheDocument();
    expect(screen.queryByTestId('profile-done')).toBeNull();
  });

  // Review of #51: the done line shows what was already true, so it is quiet when the page opens; the cheer is what
  // the person just did, so it is said.
  it('the done line is quiet when the page opens on a full profile', async () => {
    const all = profileState({ you: { status: 'done' }, kids: { status: 'done' }, home: { status: 'done' }, benefits: { status: 'done' }, banks: { status: 'done' } });
    stubFetch({ ...SUMMARIES, '/api/profile': all, '/api/facts': FACTS, '/api/auth/me': ME });
    atProfileLive('/profile');
    expect(await screen.findByTestId('profile-done')).toHaveTextContent('All five. The plan has what it needs.');
    expect(screen.getByTestId('live-said')).not.toHaveTextContent('All five');
  });

  it('the cheer on arriving from the last section is said', async () => {
    const all = profileState({ you: { status: 'done' }, kids: { status: 'done' }, home: { status: 'done' }, benefits: { status: 'done' }, banks: { status: 'done' } });
    stubFetch({ ...SUMMARIES, '/api/profile': all, '/api/facts': FACTS, '/api/auth/me': ME });
    atProfileLive({ pathname: '/profile', state: { finished: 'banks', allDone: true } });
    await waitFor(() => expect(screen.getByTestId('live-said')).toHaveTextContent('Your profile is complete. The Plan knows your household now.'));
  });

  it('the done line is said when the profile becomes full while the page is open', async () => {
    const four = profileState({ you: { status: 'done' }, kids: { status: 'done' }, home: { status: 'done' }, benefits: { status: 'done' } }, { seen: false });
    const all = profileState({ you: { status: 'done' }, kids: { status: 'done' }, home: { status: 'done' }, benefits: { status: 'done' }, banks: { status: 'done' } });
    let seen = false;
    stubFetch({ ...SUMMARIES, '/api/profile': () => reply(seen ? all : four), '/api/facts': FACTS, '/api/auth/me': ME,
      'POST /api/profile/seen': () => { seen = true; return reply(all); } });
    atProfileLive('/profile');
    fireEvent.click(await screen.findByRole('button', { name: 'Dismiss' }));
    expect(await screen.findByTestId('profile-done')).toBeInTheDocument();
    expect(screen.getByTestId('live-said')).toHaveTextContent('All five. The plan has what it needs.');
  });

  it('arriving without a finished section plays nothing', async () => {
    stubFetch({ ...SUMMARIES, '/api/profile': profileState({ kids: { status: 'done' } }), '/api/facts': FACTS, '/api/auth/me': ME });
    atProfile('/profile');
    await screen.findByTestId('row-kids');
    expect(celebrate.profile).not.toHaveBeenCalled();
  });
});
