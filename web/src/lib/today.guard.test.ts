// Issue #26: every date default and relative label uses the household's today (lib/today.ts). A day or a year built
// from the device's own clock fields comes back the way the issue found it, one screen at a time, so this reads the
// app's source and fails on the shapes that did it.
import { readdirSync, readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const src = (rel: string) => new URL(rel, import.meta.url);
const ALLOWED = new Set(['lib/today.ts']);
const FILES = readdirSync(src('../'), { recursive: true, encoding: 'utf8' })
  .map((f) => f.replace(/\\/g, '/'))
  .filter((f) => /\.tsx?$/.test(f) && !/\.test\.tsx?$/.test(f) && !/(^|\/)test-[^/]*$/.test(f) && !ALLOWED.has(f));
const read = (rel: string) => readFileSync(src(`../${rel}`), 'utf8');

const SHAPES: [string, RegExp][] = [
  ['a YYYY-MM-DD built from Date fields', /\$\{\s*\w+\.getFullYear\(\)\s*\}-/],
  ["the device's year as a default", /new Date\(\)\.getFullYear\(\)/],
  ["the device's day from a timestamp of now", /localYmd\(\s*new Date\(\)/],
  // A day or a month moved on the device calendar: the same day, the next month's 1st, this month's last day.
  ['a day moved on the device calendar', /new Date\(\s*\w+\.getFullYear\(\),\s*\w+\.getMonth\(\)/],
  // "Is it the same day?" asked of the device's calendar (plan's savedWhen did, beside a household "on Nov 16").
  ["the device's day compared", /\.toDateString\(\)/],
];

/** The arguments of a call starting at `open` (the index of its "("), split at its own commas. */
function args(text: string, open: number): string[] {
  const out: string[] = [];
  let depth = 0;
  let start = open + 1;
  for (let i = open; i < text.length; i++) {
    const c = text[i];
    if (c === '(' || c === '[' || c === '{') depth++;
    else if (c === ')' || c === ']' || c === '}') {
      depth--;
      if (depth === 0) { out.push(text.slice(start, i).trim()); return out; }
    } else if (c === ',' && depth === 1) { out.push(text.slice(start, i).trim()); start = i + 1; }
  }
  return out;
}

// The relative-label helpers take "today" as their second argument. A Date built from a year and a month is the
// device's midnight, which is another day at home for a household west of the device (Signs passed one, so all of
// January the plan sign compared against last year). Pass the day as a YYYY-MM-DD string instead.
const TODAY_TAKERS = /\b(?:dateLabel|dateLabelFromIso|relativeDays|updatedLabel)\(/g;
function deviceMidnights(text: string): string[] {
  const found: string[] = [];
  for (const m of text.matchAll(TODAY_TAKERS)) {
    const today = args(text, m.index + m[0].length - 1)[1] ?? '';
    const made = /^new Date\(/.exec(today);
    if (made && args(today, made[0].length - 1).length >= 2) found.push(`${m[0]}…, ${today})`);
  }
  return found;
}

// A household day beside a device clock time: the day says Nov 16 while the time is already Nov 17 on the device.
// lib/today's clockTime is the household's clock.
const HOUSEHOLD_DAY = /\b(?:dayOf|localYmd|dateLabelFromIso|today|thisYear)\(/;
const DEVICE_TIME = /\.toLocaleTimeString\(/;

// #52: a saved plan is named by the day the server sends (saved_day, the household's day); Taxes builds its sentence on
// the server from the same day. Turning the saved instant into a day in the browser is how Home and Plan came to differ
// from Taxes. A pattern for "saved_at handed to a day helper" misses a call split over lines, a nested call and an
// alias, so instead every mention of saved_at in app code (comments aside) must be one of these spots, each with why.
const SAVED_AT_ALLOWED: { file: string; code: string; why: string }[] = [
  { file: 'screens/plan/api.ts', code: 'id: number; saved_at: string; saved_day: string;',
    why: "SavedPlan's type: GET /plan, POST /plan and /plan/history send the instant beside the day" },
  { file: 'screens/plan/api.ts', code: 'likely_age: number | null; saved_at: string; saved_day: string }',
    why: "LastSaved's type: the 503 body's last save sends the instant beside the day" },
  { file: 'screens/plan/usePlanSave.ts', code: 'at: res.plan.saved_at,',
    why: "SavedNote.at: levers.savedWhen uses it only for the household's clock time of a save made today (clockTime)" },
  { file: 'screens/plan/fixtures.ts', code: "plan: { id: 7, saved_at: '2026-10-12T19:00:00+00:00', saved_day: '2026-10-12',",
    why: "test data shaped like GET /plan's answer" },
];

// `text` with its line and block comments blanked (newlines kept, so line numbers hold). Strings are kept.
function code(text: string): string {
  let out = '';
  let quote: string | null = null;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quote) {
      out += c;
      if (c === '\\') out += text[++i] ?? '';
      else if (c === quote) quote = null;
    } else if (c === '/' && text[i + 1] === '/') {
      while (i + 1 < text.length && text[i + 1] !== '\n') i++;
    } else if (c === '/' && text[i + 1] === '*') {
      const end = text.indexOf('*/', i + 2);
      const stop = end < 0 ? text.length : end + 2;
      out += text.slice(i, stop).replace(/[^\n]/g, ' ');
      i = stop - 1;
    } else {
      if (c === "'" || c === '"' || c === '`') quote = c;
      out += c;
    }
  }
  return out;
}

/** Each line of `text` that mentions saved_at in code and isn't an allowed spot of `rel`, as "rel:line". */
function savedAtSpots(rel: string, text: string): string[] {
  const allowed = SAVED_AT_ALLOWED.filter((a) => a.file === rel);
  const found: string[] = [];
  code(text).split('\n').forEach((line, i) => {
    const rest = allowed.reduce((l, a) => l.replace(a.code, ''), line);
    if (/\bsaved_at\b/.test(rest)) found.push(`${rel}:${i + 1}`);
  });
  return found;
}

describe("the household's today is the only today (issue #26)", () => {
  it('reads the whole app', () => { expect(FILES.length).toBeGreaterThan(300); });

  it('no screen builds a day or a year from the device clock', () => {
    const found: string[] = [];
    for (const rel of FILES) {
      read(rel).split('\n').forEach((line, i) => {
        for (const [what, re] of SHAPES) if (re.test(line)) found.push(`${rel}:${i + 1} ${what}`);
      });
    }
    expect(found).toEqual([]);
  });

  it("no relative label is handed the device's midnight as today", () => {
    expect(FILES.flatMap((rel) => deviceMidnights(read(rel)).map((call) => `${rel}: ${call}`))).toEqual([]);
  });

  it("no screen pairs the household's day with the device's clock time", () => {
    expect(FILES.filter((rel) => { const t = read(rel); return HOUSEHOLD_DAY.test(t) && DEVICE_TIME.test(t); })).toEqual([]);
  });

  it("names a saved plan by the server's saved_day, never by its instant (#52)", () => {
    const apps = readdirSync(src('../'), { recursive: true, encoding: 'utf8' })
      .map((f) => f.replace(/\\/g, '/')).filter((f) => /\.tsx?$/.test(f) && !/\.test\.tsx?$/.test(f) && !/(^|\/)test-[^/]*$/.test(f));
    expect(apps.flatMap((rel) => savedAtSpots(rel, read(rel)))).toEqual([]);
    // Each allowed spot is still there, once: one that moved or went comes off the list rather than cover another line.
    expect(SAVED_AT_ALLOWED.filter((a) => code(read(a.file)).split('\n').filter((l) => l.includes(a.code)).length !== 1)).toEqual([]);
  });

  it('catches each shape it names', () => {
    // #52's lines as they were, and the three shapes the review named: a split call, a nested call and an alias.
    expect(savedAtSpots('lib/headline.ts', 'date: dateLabelFromIso(c.saved_at, today) });')).toEqual(['lib/headline.ts:1']);
    expect(savedAtSpots('screens/plan/savedNote.ts', 'if (localYmd(n.previous.saved_at) !== today(now))')).toHaveLength(1);
    expect(savedAtSpots('screens/home/Signs.tsx', 'const label = dateLabelFromIso(\n  plan.saved_at,\n  today);')).toEqual(['screens/home/Signs.tsx:2']);
    expect(savedAtSpots('screens/plan/Overview.tsx', 'dateLabelFromIso(newest(history).saved_at, today)')).toHaveLength(1);
    expect(savedAtSpots('screens/plan/HistorySheet.tsx', 'const { saved_at: when } = p;\nreturn dateLabelFromIso(when, today);')).toHaveLength(1);
    expect(savedAtSpots('screens/plan/change.ts', "const when = p['saved_at'];")).toHaveLength(1);
    // An allowed spot passes only in its own file. The day, and saved_at in a comment, never count.
    expect(savedAtSpots('screens/plan/usePlanSave.ts', 'const n = { at: res.plan.saved_at, day: res.plan.saved_day };')).toEqual([]);
    expect(savedAtSpots('screens/plan/levers.ts', 'const n = { at: res.plan.saved_at, day: res.plan.saved_day };')).toHaveLength(1);
    expect(savedAtSpots('screens/plan/usePlanSave.ts', 'const n = { at: res.plan.saved_at, day: dayOf(res.plan.saved_at) };')).toHaveLength(1);
    expect(savedAtSpots('lib/headline.ts', "date: dateLabel(c.saved_day, today), // not c.saved_at\n/* saved_at */ const u = 'https://x';")).toEqual([]);
    // The two lines the review found, as they were.
    expect(deviceMidnights("dateLabelFromIso(p.saved_at, new Date(Number(month.slice(0, 4)), Number(month.slice(5, 7)) - 1, 1))")).toHaveLength(1);
    expect(SHAPES.some(([, re]) => re.test('if (d.toDateString() !== now.toDateString()) return x;'))).toBe(true);
    // Chat's cap date and budget pace, as they were: the next month and the month's length on the device calendar.
    expect(SHAPES.some(([, re]) => re.test('export const nextMonth = (now = new Date()) => new Date(now.getFullYear(), now.getMonth() + 1, 1)'))).toBe(true);
    expect(SHAPES.some(([, re]) => re.test('  const days = new Date(now.getFullYear(), now.getMonth() + 1, 0).getDate();'))).toBe(true);
    // An instant as today is fine, and so is a day as a string.
    expect(deviceMidnights('relativeDays(iso, new Date(now))')).toEqual([]);
    expect(deviceMidnights('dateLabelFromIso(p.saved_at, `${month}-01`)')).toEqual([]);
    expect(HOUSEHOLD_DAY.test('const key = dayOf(r.at);') && DEVICE_TIME.test("new Date(iso).toLocaleTimeString('en-US')")).toBe(true);
  });
});
