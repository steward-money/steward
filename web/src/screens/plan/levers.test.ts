import { describe, expect, it } from 'vitest';
import type { LeverState } from './api';
import { setHouseholdZone } from '../../lib/today';
import { ASSUMED, TRY, impactsFixture, planFixture } from './fixtures';
import {
  EMPTY_DRAFT, afterSave, amountText, atSuggestion, bodyKey, changedLevers, collegeTryText, deltaBody, effectClass, extrasFor, fromHistory, isPinned, rankedKeys,
  savedWhen, scaleLabels, shownValues, sliderPct, trySuggestion, tryText, turnOnCompanions, unpinLever, withAssumed, withTry, type RowCtx,
} from './levers';

const server = (pinned: LeverState['pinned'] = []): LeverState => ({ assumed: { ...ASSUMED }, try: { ...TRY }, pinned, try_notes: {} });
const ctx: RowCtx = { names: { jr: 'JR', spouse: 'Sam', joint: 'Joint' }, planYear: 2026, jrAge: 41, spouseAge: 39, younger: 'Sam', childLabels: ['Oldest', 'Middle', 'Youngest'] };

describe('deltaBody (M2-B ruling 9)', () => {
  it('sends only a moved assumed field', () => {
    expect(deltaBody(server(), withAssumed(EMPTY_DRAFT, 'save_more_monthly_cents', 50000))).toEqual({ assumed: { save_more_monthly_cents: 50000 } });
  });
  it('sends nothing once a lever is dragged back to where it was', () => {
    const d = withAssumed(withAssumed(EMPTY_DRAFT, 'spending_pct', 90), 'spending_pct', 100);
    expect(deltaBody(server(), d)).toEqual({});
  });
  it('sends every field of a lever whose try value moved, so the pin holds what is shown', () => {
    expect(deltaBody(server(), withTry(EMPTY_DRAFT, 'consulting_cents', 2400000)))
      .toEqual({ try: { consulting_cents: 2400000, consulting_until_age: 60 } });
  });
  it('unpins by sending the pinned list without that lever, and drops its try fields', () => {
    const d = unpinLever(withTry(EMPTY_DRAFT, 'consulting_cents', 2400000), 'consulting');
    expect(deltaBody(server(['consulting', 'gift']), d)).toEqual({ pinned: ['gift'] });
    expect(deltaBody(server([]), d)).toEqual({});
  });
  it('"Try this" moves only the assumed fields to the suggestion, so nothing is pinned', () => {
    const d = trySuggestion(EMPTY_DRAFT, 'consulting', TRY);
    expect(deltaBody(server(), d)).toEqual({ assumed: { consulting_cents: 1800000 } }); // until 60 already matches
    expect(isPinned(server(), d, 'consulting')).toBe(false);
    expect(deltaBody(server(), trySuggestion(EMPTY_DRAFT, 'save_more', TRY))).toEqual({ assumed: { save_more_monthly_cents: 50000 } });
  });
  it('"Change suggestion" sends the try fields and so pins that lever', () => {
    const d = withTry(EMPTY_DRAFT, 'gift_cents', 1500000);
    expect(deltaBody(server(), d)).toEqual({ try: { gift_cents: 1500000, gift_until_year: 2036 } });
    expect(isPinned(server(), d, 'gift')).toBe(true);
  });
  it('keys bodies the same whatever the key order', () => {
    expect(bodyKey({ assumed: { spending_pct: 90, save_more_monthly_cents: 5000 } })).toBe(bodyKey({ assumed: { save_more_monthly_cents: 5000, spending_pct: 90 } }));
  });
});

describe('after a save', () => {
  it('clears the levers that were saved and keeps a lever moved while the save was in flight', () => {
    const sent = withAssumed(withAssumed(EMPTY_DRAFT, 'spending_pct', 90), 'save_more_monthly_cents', 50000);
    expect(afterSave(sent, sent)).toBe(EMPTY_DRAFT);
    const now = withAssumed(sent, 'save_more_monthly_cents', 75000);
    expect(afterSave(now, sent)).toEqual({ assumed: { save_more_monthly_cents: 75000 }, try: {}, unpin: [] });
    const unpinned = unpinLever(sent, 'gift');
    expect(afterSave(unpinned, sent)).toEqual({ assumed: {}, try: {}, unpin: ['gift'] });
  });
});

describe('what the rows show', () => {
  it("overlays the draft on the saved levers, and takes an answer's try values only for unpinned levers", () => {
    const latest = { ...server(), assumed: { ...server().assumed, jr_claim_age: 68 }, try: { ...TRY, consulting_cents: 2700000, save_more_monthly_cents: 100000 } };
    const d = unpinLever(withAssumed(EMPTY_DRAFT, 'spending_pct', 85), 'consulting');
    const s = shownValues(server(['consulting']), latest, { ...d, try: { consulting_cents: 9 } });
    expect(s.assumed.spending_pct).toBe(85);
    expect(s.assumed.jr_claim_age).toBe(68);
    expect(s.try.consulting_cents).toBe(2700000);
    expect(s.try.save_more_monthly_cents).toBe(TRY.save_more_monthly_cents);
  });
  it('lists changed levers in lever order', () => {
    const d = withTry(withAssumed(EMPTY_DRAFT, 'jr_claim_age', 70), 'save_more_monthly_cents', 100000);
    expect(changedLevers(server(), d)).toEqual(['save_more', 'jr_claim']);
  });
  it('knows a pinned lever', () => {
    expect(isPinned(server(['gift']), EMPTY_DRAFT, 'gift')).toBe(true);
    expect(isPinned(server(['gift']), unpinLever(EMPTY_DRAFT, 'gift'), 'gift')).toBe(false);
    expect(isPinned(server(), withTry(EMPTY_DRAFT, 'gift_cents', 1500000), 'gift')).toBe(true);
  });
  it('knows when the slider sits at the suggestion', () => {
    expect(atSuggestion('save_more', ASSUMED, TRY)).toBe(false);
    expect(atSuggestion('save_more', { ...ASSUMED, save_more_monthly_cents: 50000 }, TRY)).toBe(true);
    expect(atSuggestion('consulting', { ...ASSUMED, consulting_cents: 1800000, consulting_until_age: 59 }, TRY)).toBe(false);
  });
  it('knows a suggestion that still follows the vault', () => {
    expect(fromHistory('consulting', false, undefined)).toBe(true);
    expect(fromHistory('consulting', true, undefined)).toBe(false);
    expect(fromHistory('gift', false, 'No history yet')).toBe(false);
    expect(fromHistory('save_more', false, undefined)).toBe(false);
  });
  it('turns on a yearly lever with its companion years from the try values', () => {
    expect(turnOnCompanions('gift_cents', 500000, ASSUMED, TRY)).toEqual({ gift_until_year: 2036 });
    expect(turnOnCompanions('spouse_earnings_cents', 100000, ASSUMED, TRY)).toEqual({ spouse_start_year: 2027 });
    expect(turnOnCompanions('save_more_monthly_cents', 5000, ASSUMED, TRY)).toEqual({});
  });
});

describe('words', () => {
  it('words each suggestion: from last year when it follows the vault, else a plain default', () => {
    expect(tryText('save_more', ASSUMED, TRY, ctx)).toBe('Try $500 a month');
    expect(tryText('spending', ASSUMED, TRY, ctx)).toBe('Try 90% of current spending');
    expect(tryText('consulting', ASSUMED, TRY, ctx, true)).toBe('Your consulting last year was $18,000');
    expect(tryText('consulting', ASSUMED, TRY, ctx)).toBe('Try $18,000 a year until 60');
    expect(tryText('gift', ASSUMED, TRY, ctx, true)).toBe('Your gift last year was $10,000');
    expect(tryText('gift', ASSUMED, TRY, ctx)).toBe('Try $10,000 a year until 2036');
    expect(tryText('spouse_earnings', ASSUMED, TRY, ctx)).toBe('Try $1,000 a year from 2027 until Sam is 65');
    expect(tryText('jr_claim', ASSUMED, TRY, ctx)).toBe('Try claiming at 70');
    expect(tryText('full_ss', ASSUMED, TRY, ctx)).toBe('Try full benefits, no cut from 2034');
  });
  it('words college', () => {
    expect(collegeTryText(['in_state_public', 'in_state_public', 'in_state_public'], ['in_state_public', 'in_state_public', 'in_state_public'], ctx.childLabels)).toBe('All three already at in-state public');
    expect(collegeTryText(['private', 'in_state_public'], ['out_of_state_public', 'in_state_public'], ['Oldest', 'Middle'])).toBe('Try out-of-state public for Oldest');
    expect(collegeTryText(['private', 'private'], ['in_state_public', 'out_of_state_public'], ['Oldest', 'Middle'])).toBe('Try a different level for two children');
  });
  it('words the remaining college and suggestion branches', () => {
    expect(collegeTryText(['private', 'in_state_public'], ['private', 'in_state_public'], ctx.childLabels)).toBe('Same levels as the plan');
    expect(collegeTryText(['private'], ['private'], ['Oldest'])).toBe('Already at private');
    expect(tryText('full_ss', ASSUMED, { ...TRY, full_ss_benefits: false }, ctx)).toBe('Try the cut from 2034');
    expect(tryText('spouse_claim', ASSUMED, TRY, ctx)).toBe(`Try claiming at ${TRY.spouse_claim_age}`);
  });
  it('words the assumed amount of each lever', () => {
    expect(amountText('save_more_monthly_cents', 50000)).toBe('$500 a month');
    expect(amountText('spending_pct', 90)).toBe('90% of current spending');
    expect(amountText('gift_cents', 0)).toBe('Off');
    expect(amountText('gift_cents', 1000000)).toBe('$10,000 a year');
  });
  it('labels scales and places thumbs', () => {
    const specs = planFixture().lever_specs;
    expect(scaleLabels(specs[0])).toEqual(['$0', '$1,500', '$3,000']);
    expect(scaleLabels(specs[1])).toEqual(['60%', '95%', '130%']);
    expect(scaleLabels(specs[5])).toEqual(['62', '66', '70']);
    expect(sliderPct(50000, { lo: 0, hi: 300000, step: 5000 })).toBe(16.7);
  });
  it("offers the extra choices for consulting, the gift and Sam's earnings", () => {
    expect(extrasFor('consulting', ctx)[0]).toMatchObject({ field: 'consulting_until_age', label: 'Until JR is' });
    expect(extrasFor('consulting', ctx)[0].options[0]).toBe(41);
    expect(extrasFor('spouse_earnings', ctx).map((x) => x.label)).toEqual(['Starting in', 'Until Sam is']);
    expect(extrasFor('save_more', ctx)).toEqual([]);
  });
  it('ranks levers by their first impact row, once each', () => {
    expect(rankedKeys(impactsFixture())).toEqual(['spending', 'save_more', 'consulting', 'full_ss', 'gift', 'jr_claim', 'spouse_earnings', 'spouse_claim', 'college']);
  });
  it('colours effects', () => {
    const [first] = impactsFixture();
    expect(effectClass(first)).toBe('is-earlier');
    expect(effectClass({ ...first, delta_months: 0 })).toBe('is-none');
    expect(effectClass({ ...first, delta_months: null, text: 'Not by 70' })).toBe('is-later');
    expect(effectClass({ ...first, delta_months: null, text: 'Comfortable by 64' })).toBe('is-earlier');
    expect(effectClass({ ...first, delta_months: null, text: 'Still not by 70' })).toBe('is-none');
    expect(effectClass(undefined)).toBe('is-pending');
  });
  it('says when a plan was saved', () => {
    const at = new Date(2026, 9, 14, 21, 14).toISOString();
    expect(savedWhen(at, '2026-10-14', new Date(2026, 9, 14, 22, 0))).toBe('today at 9:14 pm');
    expect(savedWhen(new Date(2026, 9, 12, 9, 0).toISOString(), '2026-10-12', new Date(2026, 9, 14, 22, 0))).toBe('on Oct 12');
  });
  it("says today or the day, and the time, on the household's clock (#26)", () => {
    setHouseholdZone('Pacific/Kiritimati');                        // UTC+14: noon UTC on Nov 16 is 2 AM on Nov 17
    const now = new Date('2026-11-16T12:00:00Z');
    expect(savedWhen('2026-11-16T11:00:00Z', '2026-11-17', now)).toBe('today at 1:00 am');
    // 11 PM on Nov 16 at home, though the same calendar day as now on most devices.
    expect(savedWhen('2026-11-16T09:00:00Z', '2026-11-16', now)).toBe('on Nov 16');
  });
});
