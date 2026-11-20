// The lever model: the delta body the API expects (M2-B ruling 9), what each row shows, and its words.
// One slider per lever (design decision 4, spec 3.6 amendment): the slider is the assumed value; the try value is the
// suggestion in the closed row, moved onto the slider by "Try this" and pinned only through "Change suggestion".
import { dateLabel, dollars } from '../../lib/format';
import { clockTime, today } from '../../lib/today';
import type { CostLevel, DeltaBody, Impact, LeverField, LeverKey, LeverSpec, LeverState, LeverValue, LeverValues, PlanCollege, Scale } from './api';
import { LEVEL_NAME } from './copy';
import type { People } from './people';

export const FIELDS_OF: Record<LeverKey, LeverField[]> = {
  save_more: ['save_more_monthly_cents'],
  spending: ['spending_pct'],
  consulting: ['consulting_cents', 'consulting_until_age'],
  gift: ['gift_cents', 'gift_until_year'],
  spouse_earnings: ['spouse_earnings_cents', 'spouse_start_year', 'spouse_stop_age'],
  jr_claim: ['jr_claim_age'],
  spouse_claim: ['spouse_claim_age'],
  college: ['college_levels'],
  full_ss: ['full_ss_benefits'],
};
export const ORDER = Object.keys(FIELDS_OF) as LeverKey[];
export const LEVER_OF = Object.fromEntries(ORDER.flatMap((k) => FIELDS_OF[k].map((f) => [f, k]))) as Record<LeverField, LeverKey>;
/** The try values that follow the vault until someone pins them (spec 3.6). */
export const UNPINNABLE: LeverKey[] = ['consulting', 'gift'];

export type Draft = { assumed: Partial<LeverValues>; try: Partial<LeverValues>; unpin: LeverKey[] };
export const EMPTY_DRAFT: Draft = { assumed: {}, try: {}, unpin: [] };

type Loose = Record<string, unknown>;
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

function changed(base: LeverValues, part: Partial<LeverValues>): Partial<LeverValues> {
  const out: Loose = {};
  for (const [f, v] of Object.entries(part)) if (!same(v, base[f as LeverField])) out[f] = v;
  return out as Partial<LeverValues>;
}

/** Only what someone moved; every field of a lever whose try moved; `pinned` only to unpin. */
export function deltaBody(server: LeverState, draft: Draft): DeltaBody {
  const body: DeltaBody = {};
  const assumed = changed(server.assumed, draft.assumed);
  if (Object.keys(assumed).length) body.assumed = assumed;
  const moved = new Set(Object.keys(changed(server.try, draft.try)).map((f) => LEVER_OF[f as LeverField]));
  for (const k of draft.unpin) moved.delete(k);
  if (moved.size) {
    const trial: Loose = {};
    for (const k of ORDER) if (moved.has(k)) for (const f of FIELDS_OF[k]) trial[f] = f in draft.try ? draft.try[f] : server.try[f];
    body.try = trial as Partial<LeverValues>;
  }
  const unpins = draft.unpin.filter((k) => server.pinned.includes(k));
  if (unpins.length) body.pinned = server.pinned.filter((k) => !unpins.includes(k));
  return body;
}

export const isEmptyBody = (b: DeltaBody) => Object.keys(b).length === 0;

export function bodyKey(b: DeltaBody): string {
  return JSON.stringify(b, (_k, v: unknown) =>
    v && typeof v === 'object' && !Array.isArray(v) ? Object.fromEntries(Object.entries(v).sort(([x], [y]) => x.localeCompare(y))) : v);
}

export const withAssumed = (d: Draft, f: LeverField, v: LeverValue): Draft => ({ ...d, assumed: { ...d.assumed, [f]: v } });
export const withTry = (d: Draft, f: LeverField, v: LeverValue): Draft =>
  ({ ...d, try: { ...d.try, [f]: v }, unpin: d.unpin.filter((k) => k !== LEVER_OF[f]) });
export function unpinLever(d: Draft, key: LeverKey): Draft {
  const trial: Loose = { ...d.try };
  for (const f of FIELDS_OF[key]) delete trial[f];
  return { ...d, try: trial as Partial<LeverValues>, unpin: d.unpin.includes(key) ? d.unpin : [...d.unpin, key] };
}

/** "Try this": the lever's assumed fields take its try values. No try field moves, so nothing is pinned. */
export function trySuggestion(d: Draft, key: LeverKey, trial: LeverValues): Draft {
  let next = d;
  for (const f of FIELDS_OF[key]) next = withAssumed(next, f, trial[f]);
  return next;
}

/** After a save of `sent`: drop the levers that were saved, keep any lever moved while the save was in flight. */
export function afterSave(current: Draft, sent: Draft): Draft {
  const moved = (k: LeverKey) => FIELDS_OF[k].some((f) => !same(current.assumed[f], sent.assumed[f]) || !same(current.try[f], sent.try[f]))
    || current.unpin.includes(k) !== sent.unpin.includes(k);
  const keep = new Set(ORDER.filter(moved));
  const pick = (part: Partial<LeverValues>) => Object.fromEntries(Object.entries(part).filter(([f]) => keep.has(LEVER_OF[f as LeverField]))) as Partial<LeverValues>;
  const next: Draft = { assumed: pick(current.assumed), try: pick(current.try), unpin: current.unpin.filter((k) => keep.has(k)) };
  return keep.size === 0 ? EMPTY_DRAFT : next;
}

export const atSuggestion = (key: LeverKey, a: LeverValues, t: LeverValues) => FIELDS_OF[key].every((f) => same(a[f], t[f]));

/** The suggestion still follows the vault (spec 3.6): consulting or the gift, not pinned, with a history. */
export const fromHistory = (key: LeverKey, pinned: boolean, note: string | undefined) => UNPINNABLE.includes(key) && !pinned && !note;

/** Turning on a yearly lever that was off also sets its unset year (0) from the try value. */
export function turnOnCompanions(field: LeverField, v: LeverValue, assumed: LeverValues, trial: LeverValues): Partial<LeverValues> {
  const was = assumed[field];
  if (typeof v !== 'number' || v === 0 || was !== 0) return {};
  if (field === 'gift_cents' && assumed.gift_until_year === 0) return { gift_until_year: trial.gift_until_year };
  if (field === 'spouse_earnings_cents' && assumed.spouse_start_year === 0) return { spouse_start_year: trial.spouse_start_year };
  return {};
}

/** The try values come from the saved plan and the draft, except unpinned levers, which take the latest answer's vault value. A preview's own suggestion must not move "Try this". */
export function shownValues(server: LeverState, latest: LeverState | undefined, draft: Draft): { assumed: LeverValues; try: LeverValues } {
  const base = latest ?? server;
  const trial: Loose = { ...server.try, ...draft.try };
  // An unpinned lever follows the vault: the latest answer already saw the unpin, the saved plan still holds the pin.
  for (const k of draft.unpin) for (const f of FIELDS_OF[k]) trial[f] = (latest ?? server).try[f];
  return { assumed: { ...base.assumed, ...draft.assumed }, try: trial as LeverValues };
}

export function changedLevers(server: LeverState, draft: Draft): LeverKey[] {
  const b = deltaBody(server, draft);
  const keys = new Set<LeverKey>();
  for (const f of Object.keys(b.assumed ?? {})) keys.add(LEVER_OF[f as LeverField]);
  for (const f of Object.keys(b.try ?? {})) keys.add(LEVER_OF[f as LeverField]);
  for (const k of draft.unpin) if (server.pinned.includes(k)) keys.add(k);
  return ORDER.filter((k) => keys.has(k));
}

export function isPinned(server: LeverState, draft: Draft, key: LeverKey): boolean {
  const moved = FIELDS_OF[key].some((f) => f in draft.try && !same(draft.try[f], server.try[f]));
  return moved || (server.pinned.includes(key) && !draft.unpin.includes(key));
}

export type RowCtx = People & { childLabels: string[]; college?: PlanCollege };
const NUMBER_WORDS = ['zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine'];
const count = (n: number) => NUMBER_WORDS[n] ?? String(n);

export function amountText(field: LeverField, v: number): string {
  switch (field) {
    case 'save_more_monthly_cents': return `${dollars(v)} a month`;
    case 'spending_pct': return `${v}% of current spending`;
    case 'consulting_cents': case 'gift_cents': case 'spouse_earnings_cents': return v === 0 ? 'Off' : `${dollars(v)} a year`;
    default: return String(v);
  }
}

export function collegeTryText(a: CostLevel[], t: CostLevel[], labels: string[]): string {
  const diff = t.map((l, i) => (l !== a[i] ? i : -1)).filter((i) => i >= 0);
  if (diff.length === 0) {
    if (a.length > 0 && a.every((l) => l === a[0])) {
      const level = LEVEL_NAME[a[0]].toLowerCase();
      return a.length === 1 ? `Already at ${level}` : `All ${count(a.length)} already at ${level}`;
    }
    return 'Same levels as the plan';
  }
  if (diff.length === 1) return `Try ${LEVEL_NAME[t[diff[0]]].toLowerCase()} for ${labels[diff[0]] ?? 'one child'}`;
  return `Try a different level for ${count(diff.length)} children`;
}

/** The closed row's suggestion. `history`: it follows the vault, so it says what last year was (design decision 4). */
export function tryText(key: LeverKey, a: LeverValues, t: LeverValues, ctx: RowCtx, history = false): string {
  switch (key) {
    case 'save_more': return `Try ${dollars(t.save_more_monthly_cents)} a month`;
    case 'spending': return `Try ${t.spending_pct}% of current spending`;
    case 'consulting': return history ? `Your consulting last year was ${dollars(t.consulting_cents)}` : `Try ${dollars(t.consulting_cents)} a year until ${t.consulting_until_age}`;
    case 'gift': return history ? `Your gift last year was ${dollars(t.gift_cents)}` : `Try ${dollars(t.gift_cents)} a year until ${t.gift_until_year}`;
    case 'spouse_earnings': return `Try ${dollars(t.spouse_earnings_cents)} a year from ${t.spouse_start_year} until ${ctx.names.spouse} is ${t.spouse_stop_age}`;
    case 'jr_claim': return `Try claiming at ${t.jr_claim_age}`;
    case 'spouse_claim': return `Try claiming at ${t.spouse_claim_age}`;
    case 'full_ss': return t.full_ss_benefits ? 'Try full benefits, no cut from 2034' : 'Try the cut from 2034';
    case 'college': return collegeTryText(a.college_levels ?? [], t.college_levels ?? [], ctx.childLabels);
  }
}

export function sliderPct(v: number, s: Scale): number {
  if (s.hi === s.lo) return 0;
  return Math.round(Math.min(100, Math.max(0, ((v - s.lo) / (s.hi - s.lo)) * 100)) * 10) / 10;
}

export function scaleLabels(spec: LeverSpec): [string, string, string] {
  const s = spec.scale!;
  const mid = s.lo + Math.round((s.hi - s.lo) / 2 / s.step) * s.step;
  const fmt = (v: number) => (spec.kind === 'money' ? dollars(v) : spec.kind === 'pct' ? `${v}%` : String(v));
  return [fmt(s.lo), fmt(mid), fmt(s.hi)];
}

const range = (a: number, b: number) => Array.from({ length: Math.max(0, b - a + 1) }, (_, i) => a + i);

export function extrasFor(key: LeverKey, ctx: RowCtx): { field: LeverField; label: string; options: number[] }[] {
  switch (key) {
    case 'consulting': return [{ field: 'consulting_until_age', label: `Until ${ctx.names.jr} is`, options: range(ctx.jrAge, 70) }];
    case 'gift': return [{ field: 'gift_until_year', label: 'Until', options: range(ctx.planYear, ctx.planYear + 30) }];
    case 'spouse_earnings': return [
      { field: 'spouse_start_year', label: 'Starting in', options: range(ctx.planYear, ctx.planYear + 20) },
      { field: 'spouse_stop_age', label: `Until ${ctx.names.spouse} is`, options: range(ctx.spouseAge, 70) },
    ];
    default: return [];
  }
}

export function effectClass(i: Impact | undefined): string {
  if (!i) return 'is-pending';
  if (i.delta_months === null) return i.text.startsWith('Comfortable by') ? 'is-earlier' : i.text === 'Still not by 70' ? 'is-none' : 'is-later';
  return i.delta_months < 0 ? 'is-earlier' : i.delta_months > 0 ? 'is-later' : 'is-none';
}

export const impactFor = (impacts: Impact[] | undefined, key: LeverKey) => impacts?.find((i) => i.key === key);

/** The server sorts impacts by size; a lever's rank is its first row (college has one row per child). */
export function rankedKeys(impacts: Impact[]): LeverKey[] {
  const seen: LeverKey[] = [];
  for (const i of impacts) if (!seen.includes(i.key)) seen.push(i.key);
  for (const k of ORDER) if (!seen.includes(k)) seen.push(k);
  return seen;
}

/** "today at 3:10 pm" or "on Nov 16": `day` is the save's household day from the server (#52), the time is on the
 *  household's clock (issue #26), so the two halves never disagree near midnight. */
export function savedWhen(iso: string, day: string, now: Date): string {
  if (day !== today(now)) return `on ${dateLabel(day, now)}`;
  const time = clockTime(iso).replace(/\s?([AP])M$/, (_m, x: string) => ` ${x.toLowerCase()}m`);
  return `today at ${time}`;
}
