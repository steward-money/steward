import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { callsTo, renderAt, reply, stubFetch } from '../../test-utils';
import { acct } from '../accounts/fixtures';
import { MERGE_REPLY, MERGED_TEXT } from './fixtures';
import MergeInto, { MergedNote } from './MergeInto';

beforeEach(() => { localStorage.clear(); localStorage.setItem('steward-celebrations', 'off'); });
afterEach(() => vi.unstubAllGlobals());

const JOINT = acct({ id: 5, name: 'Joint Taxable', type: 'brokerage_taxable', institution: 'Betterment', balance_cents: 8421000 });
const OLDER = acct({ id: 3, name: 'Joint Taxable', type: 'brokerage_taxable', institution: 'Betterment', balance_cents: 8421000 });
const SECOND = acct({ id: 9, name: 'JR Brokerage', type: 'brokerage_taxable', institution: 'Betterment', balance_cents: 120000 });
const PREVIEW = { merged: { kept: { id: 3, name: 'Joint Taxable' }, dropped: 61, kept_new: 3 } };

describe('Merge into… (same-account spec, Merging)', () => {
  it('lists the choices, previews the chosen one and confirms with its name', async () => {
    const fetchMock = stubFetch({ '/api/accounts/5/merge-preview': PREVIEW, 'POST /api/accounts/5/merge-into': MERGE_REPLY });
    const onMerged = vi.fn();
    renderAt(<MergeInto account={JOINT} others={[OLDER, SECOND]} onClose={() => {}} onMerged={onMerged} />, {}, '/accounts/5');
    const sheet = screen.getByRole('dialog', { name: 'Merge Joint Taxable into…' });
    await waitFor(() => expect(within(sheet).getByRole('heading', { name: 'Merge Joint Taxable into…' })).toHaveFocus());
    expect(within(sheet).getByRole('button', { name: 'Merge into…' })).toBeDisabled();
    fireEvent.click(within(sheet).getByRole('radio', { name: /^Joint Taxable/ }));
    const preview = await within(sheet).findByTestId('merge-preview');
    expect(preview).toHaveTextContent('61 transactions match and will be dropped; 3 will move over.');
    // Issue #51: said through the sheet's polite region; the line is not a live region that appears with its words.
    expect(preview).not.toHaveAttribute('role');
    expect(within(sheet).getByTestId('live-said')).toHaveTextContent('61 transactions match and will be dropped; 3 will move over.');
    expect(callsTo(fetchMock, 'GET', '/api/accounts/5/merge-preview')[0].url).toContain('into=3');
    fireEvent.click(within(sheet).getByRole('button', { name: 'Merge into Joint Taxable' }));
    await waitFor(() => expect(onMerged).toHaveBeenCalledWith(MERGE_REPLY));
    expect(callsTo(fetchMock, 'POST', '/api/accounts/5/merge-into')[0].body).toEqual({ into: 3 });
  });

  it('with only one choice it is chosen already, and the server says what it refused', async () => {
    stubFetch({ '/api/accounts/5/merge-preview': () => reply({ error: 'Choose an account of the same type.' }, 422) });
    renderAt(<MergeInto account={JOINT} others={[SECOND]} onClose={() => {}} onMerged={() => {}} />, {}, '/accounts/5');
    expect(screen.getByRole('radio', { name: /^JR Brokerage/ })).toBeChecked();
    expect(await screen.findByRole('alert')).toHaveTextContent('Choose an account of the same type.');
    expect(screen.getByRole('button', { name: 'Merge into JR Brokerage' })).toBeDisabled();
  });

  it("the kept account's page says what happened, and Undo puts it back", async () => {
    const fetchMock = stubFetch({ 'POST /api/undo/tok': { undone: 61, transaction: null } });
    renderAt(<MergedNote reply={MERGE_REPLY} />, {}, '/accounts/3');
    expect(screen.getByText(MERGED_TEXT)).toBeInTheDocument();
    expect(screen.getByText('One account, as it should be.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Undo' }));
    expect(await screen.findByText('Undone.')).toBeInTheDocument();
    expect(callsTo(fetchMock, 'POST', '/api/undo/tok')).toHaveLength(1);
  });
});
