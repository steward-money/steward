import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { callsTo, reply, stubFetch } from '../../../test-utils';
import { ME, atProfile, atProfileLive, profileState } from '../fixtures';

afterEach(() => vi.unstubAllGlobals());
const none = { source: 'none', years: 0, first: null, last: null, at_67_cents: null };
const benefits = (over = {}) => ({ jr: none, spouse: none, health: { premium_cents: null, suggested_cents: 1_680_000, part_b_monthly_cents: 20_290, rules_year: 2026 }, ...over });

describe('Social Security and Medicare', () => {
  it('says why, offers a record or the estimate, and shows each line', async () => {
    const fetchMock = stubFetch({ '/api/profile': profileState(), '/api/auth/me': ME, 'POST /api/facts': () => reply({ undo: 'e' }, 201),
      '/api/profile/benefits': benefits({ jr: { source: 'estimate', years: 22, first: 2004, last: 2025, at_67_cents: 310_000 },
        spouse: { source: 'estimate', years: 0, first: null, last: null, at_67_cents: 155_000 } }) });
    atProfile('/profile/benefits/ss');
    expect(await screen.findByText('Your benefit is set by your earnings in every year you worked. The Plan counts it from the age you claim.')).toBeInTheDocument();
    expect(screen.getByText("Estimated from JR's salary: about $3,100 a month at 67, in today's dollars.")).toBeInTheDocument();
    expect(screen.getByText('Without a record, Alex gets the spousal benefit: about $1,550 a month at 67.')).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: 'Add earnings record' })).toHaveLength(2);
    expect(callsTo(fetchMock, 'POST', '/api/facts')).toHaveLength(0);
  });

  it('a viewer reads the card: no estimate or earnings-record buttons, no file input, only the back link', async () => {
    stubFetch({ '/api/profile': profileState(), '/api/auth/me': ME, '/api/profile/benefits': benefits({ jr: { source: 'estimate', years: 22, first: 2004, last: 2025, at_67_cents: 310_000 } }) });
    atProfile('/profile/benefits', { role: 'viewer' });
    expect(await screen.findByText("Estimated from JR's salary: about $3,100 a month at 67, in today's dollars.")).toBeInTheDocument();
    expect(await screen.findByText('Yearly premium for the household')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Alex' })).toBeInTheDocument();
    expect(screen.queryByRole('button')).toBeNull();
    expect(document.querySelectorAll('select, input, textarea, button')).toHaveLength(0);
    expect(screen.getAllByRole('link').map((l) => l.textContent)).toEqual(['Profile']);
  });

  it("Use Steward's estimate saves the fact for that person", async () => {
    const fetchMock = stubFetch({ '/api/profile': profileState(), '/api/auth/me': ME, '/api/profile/benefits': benefits(), 'POST /api/facts': () => reply({ undo: 'e' }, 201) });
    atProfile('/profile/benefits/ss');
    fireEvent.click((await screen.findAllByRole('button', { name: "Use Steward's estimate" }))[0]);
    await waitFor(() => expect(callsTo(fetchMock, 'POST', '/api/facts')[0]?.body).toMatchObject({ key: 'jr.ss_estimate', value: 'yes' }));
  });

  it('health says why with Part B, prefills the estimate with its source, and Done confirms it', async () => {
    const fetchMock = stubFetch({ '/api/profile': profileState(), '/api/auth/me': ME, '/api/profile/benefits': benefits(),
      'POST /api/facts': () => reply({ undo: 'p' }, 201), 'POST /api/profile/sections/benefits': () => reply(profileState({ benefits: { status: 'done' } })) });
    atProfile('/profile/benefits/health');
    expect(await screen.findByText('Between retirement and 65 you buy your own health insurance. From 65, Medicare Part B is $203 a month each (2026).')).toBeInTheDocument();
    expect(screen.getByText("Steward's estimate: a typical Covered California Silver plan for your household, before subsidies.")).toBeInTheDocument();
    expect(screen.getByLabelText('Yearly premium for the household')).toHaveValue('16,800');
    expect(screen.getByText('About $1,400 a month.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Done' }));
    await waitFor(() => expect(callsTo(fetchMock, 'POST', '/api/facts')[0]?.body).toMatchObject({ key: 'health.premium_cents', value: '1680000', note: 'Confirmed by JR' }));
  });

  it('Done on the estimate names what it saved with Undo, stays on the step, and Undo posts the token', async () => {
    const fetchMock = stubFetch({ '/api/profile': profileState(), '/api/auth/me': ME, '/api/profile/benefits': benefits(),
      'POST /api/facts': () => reply({ undo: 'p' }, 201), 'POST /api/undo/p': () => reply({}), 'POST /api/profile/sections/benefits': () => reply(profileState()) });
    atProfile('/profile/benefits/health');
    await screen.findByLabelText('Yearly premium for the household');
    fireEvent.click(screen.getByRole('button', { name: 'Done' }));
    expect(await screen.findByText('Saved the estimate: $16,800 a year')).toBeInTheDocument();
    expect(callsTo(fetchMock, 'POST', '/api/profile/sections/benefits')).toHaveLength(0);
    fireEvent.click(within(screen.getByText('Saved the estimate: $16,800 a year').parentElement as HTMLElement).getByRole('button', { name: 'Undo' }));
    await waitFor(() => expect(callsTo(fetchMock, 'POST', '/api/undo/p')).toHaveLength(1));
  });

  it('Done leaves the premium alone when it is already set', async () => {
    const fetchMock = stubFetch({ '/api/profile': profileState(), '/api/auth/me': ME,
      '/api/profile/benefits': benefits({ health: { premium_cents: 2_400_000, suggested_cents: 1_680_000, part_b_monthly_cents: 20_290, rules_year: 2026 } }),
      'POST /api/facts': () => reply({ undo: 'p' }, 201), 'POST /api/profile/sections/benefits': () => reply(profileState({ benefits: { status: 'done' } })) });
    atProfile('/profile/benefits/health');
    await screen.findByLabelText('Yearly premium for the household');
    fireEvent.click(screen.getByRole('button', { name: 'Done' }));
    await waitFor(() => expect(callsTo(fetchMock, 'POST', '/api/profile/sections/benefits')).toHaveLength(1));
    expect(callsTo(fetchMock, 'POST', '/api/facts')).toHaveLength(0);
  });

  it("Use Steward's estimate offers Undo; Undo posts the token, and a failed Undo keeps Undo and says why", async () => {
    const fetchMock = stubFetch({ '/api/profile': profileState(), '/api/auth/me': ME, '/api/profile/benefits': benefits(),
      'POST /api/facts': () => reply({ undo: 'e' }, 201),
      'POST /api/undo/e': () => reply({ error: "That changed since, so it can't be undone." }, 409) });
    atProfile('/profile/benefits/ss');
    fireEvent.click((await screen.findAllByRole('button', { name: "Use Steward's estimate" }))[0]);
    const toast = (await screen.findByText("Using Steward's estimate for JR")).parentElement as HTMLElement;
    fireEvent.click(within(toast).getByRole('button', { name: 'Undo' }));
    await waitFor(() => expect(callsTo(fetchMock, 'POST', '/api/undo/e')).toHaveLength(1));
    expect(await screen.findByText("That changed since, so it can't be undone.")).toBeInTheDocument();
    expect(screen.getByText("Using Steward's estimate for JR").parentElement?.querySelector('button')).toHaveTextContent('Undo');
  });

  describe('adding an earnings record', () => {
    const upload = () => fireEvent.change(screen.getByTestId('earnings-file-jr'), { target: { files: [new File(['<x/>'], 'e.xml', { type: 'text/xml' })] } });
    it('refreshes, shows the new line and offers Undo with the import token', async () => {
      const fetchMock = stubFetch({ '/api/profile': profileState(), '/api/auth/me': ME, '/api/profile/benefits': benefits(),
        'POST /api/ss/import': () => reply({ person: 'jr', years: 3, first_year: 2005, last_year: 2007, skipped: 0, undo: 'r' }, 201), 'POST /api/undo/r': () => reply({}) });
      atProfile('/profile/benefits/ss');
      await screen.findAllByRole('button', { name: 'Add earnings record' });
      upload();
      const toast = (await screen.findByText('Added the earnings record for JR')).parentElement as HTMLElement;
      expect(callsTo(fetchMock, 'GET', '/api/profile/benefits').length).toBeGreaterThan(1);
      fireEvent.click(within(toast).getByRole('button', { name: 'Undo' }));
      await waitFor(() => expect(callsTo(fetchMock, 'POST', '/api/undo/r')).toHaveLength(1));
    });

    // Review of #51: the line shows what was saved, so it is quiet when the step opens; a record just added is said.
    it('the line is quiet when the step opens and said when a record changes it', async () => {
      const record = { source: 'record', years: 3, first: 2005, last: 2007, at_67_cents: 210_000 };
      let added = false;
      stubFetch({ '/api/profile': profileState(), '/api/auth/me': ME,
        '/api/profile/benefits': () => reply(benefits({ jr: added ? record : { source: 'estimate', years: 22, first: 2004, last: 2025, at_67_cents: 310_000 } })),
        'POST /api/ss/import': () => { added = true; return reply({ person: 'jr', years: 3, first_year: 2005, last_year: 2007, skipped: 0, undo: 'r' }, 201); } });
      atProfileLive('/profile/benefits/ss');
      expect(await screen.findByText("Estimated from JR's salary: about $3,100 a month at 67, in today's dollars.")).toBeInTheDocument();
      expect(screen.getByTestId('live-said')).not.toHaveTextContent('Estimated');
      upload();
      await waitFor(() => expect(screen.getByTestId('live-said')).toHaveTextContent("3 years, 2005 to 2007. About $2,100 a month at 67, in today's dollars."));
    });

    it('a line that appears after the step opened is said', async () => {
      const record = { source: 'record', years: 3, first: 2005, last: 2007, at_67_cents: 210_000 };
      let added = false;
      stubFetch({ '/api/profile': profileState(), '/api/auth/me': ME,
        '/api/profile/benefits': () => reply(benefits(added ? { jr: record } : {})),
        'POST /api/ss/import': () => { added = true; return reply({ person: 'jr', years: 3, first_year: 2005, last_year: 2007, skipped: 0, undo: 'r' }, 201); } });
      atProfileLive('/profile/benefits/ss');
      await screen.findAllByRole('button', { name: 'Add earnings record' });
      upload();
      await waitFor(() => expect(screen.getByTestId('live-said')).toHaveTextContent("3 years, 2005 to 2007. About $2,100 a month at 67, in today's dollars."));
    });

    it('a 422 shows the message and that nothing was saved', async () => {
      stubFetch({ '/api/profile': profileState(), '/api/auth/me': ME, '/api/profile/benefits': benefits(),
        'POST /api/ss/import': () => reply({ error: "That isn't a Social Security earnings record." }, 422) });
      atProfile('/profile/benefits/ss');
      await screen.findAllByRole('button', { name: 'Add earnings record' });
      upload();
      expect(await screen.findByRole('alert')).toHaveTextContent("That isn't a Social Security earnings record. Nothing was saved.");
      expect(screen.queryByText('Added the earnings record for JR')).not.toBeInTheDocument();
    });
  });
});
