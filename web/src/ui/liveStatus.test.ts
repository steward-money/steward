// Issues #23 and #51: a live region (role="status", aria-live="polite" or "assertive") that mounts with its words
// already in it is often not announced. A line that appears says its words through the page's polite region instead
// (Live.tsx: Shown, useSayShown). The live regions left are below, each with the reason it is in the page before its
// words; the list may only shrink. A new line to announce uses Shown, or a region that is in the page before its words
// (as Review's review-said is).
import { describe, expect, it } from 'vitest';

const SOURCES: Record<string, string> = Object.fromEntries(
  Object.entries(import.meta.glob<string>('/src/**/*.tsx', { query: '?raw', import: 'default', eager: true }))
    .filter(([path]) => !/\.test\.[jt]sx?$/.test(path) && path !== '/src/test-utils.tsx'),
);

const LIVE = /role=(?:"status"|\{[^}]*'status'[^}]*\})|aria-live=(?:"(?:polite|assertive)"|\{[^}]*'(?:polite|assertive)'[^}]*\})/g;

/** The live regions in `src`, one per element: a role and an aria-live on the same tag count once. */
function regions(src: string): number {
  const found = [...src.matchAll(LIVE)];
  return found.filter((m, i) => {
    if (i === 0) return true;
    const prev = found[i - 1];
    const between = src.slice(prev.index + prev[0].length, m.index).replace(/=>|>=|<=/g, '');
    return /[<>]/.test(between);
  }).length;
}

/** The live regions left, per file, and why each already announces. */
const KNOWN: Record<string, number> = {
  '/src/screens/Review.tsx': 1,         // review-said: always mounted, its words change
  '/src/screens/import/Import.tsx': 1,  // the reading line: in the page before each read starts, its words change with each read
  '/src/screens/import/Statements.tsx': 2, // each review's gap line: focus moves to it when the review opens, its words change as rows are fixed
  '/src/screens/plan/Ages.tsx': 1,      // in the page with the plan, its words change when the answer does
  '/src/screens/plan/TryBar.tsx': 1,    // in the page with the levers, its words change as one moves
  '/src/screens/profile/kids/Cover.tsx': 1, // the kid line: in the page with the fields, its words change as they are edited
  '/src/screens/profile/kids/Plans.tsx': 1, // the kid line: in the page with the fields, its words change as they are edited
  '/src/ui/CopyButton.tsx': 1,          // in the page before the press; its words change
  '/src/ui/Live.tsx': 2,                // the page's toast and said regions: in the page from the start, empty
  '/src/ui/RemovalLine.tsx': 1,         // in the page before the removal, empty; the line is added inside it
  '/src/ui/UndoToast.tsx': 1,           // only outside LiveRegions (a component test)
};

/** Files whose count is not what the list says: a region removed lowers the list, so it never allows one back. */
function stale(sources: Record<string, string>): string[] {
  return Object.entries(KNOWN).flatMap(([path, n]) => {
    const now = regions(sources[path] ?? '');
    return now !== n ? [`${path}: ${now}, listed ${n}`] : [];
  });
}

function grown(sources: Record<string, string>): string[] {
  return Object.entries(sources).flatMap(([path, src]) => {
    const n = regions(src);
    return n > (KNOWN[path] ?? 0) ? [`${path}: ${n}`] : [];
  });
}

describe('no new live region that mounts with its words (issues #23, #51)', () => {
  it('Notice has none and the caught-up state has its one', () => {
    expect(regions(SOURCES['/src/ui/Notice.tsx'])).toBe(0);
    expect(regions(SOURCES['/src/screens/Review.tsx'])).toBe(1);
  });

  it('no file has more than it had', () => {
    expect(grown(SOURCES)).toEqual([]);
  });

  it('the list is lowered as each goes (issue #51)', () => {
    expect(stale(SOURCES)).toEqual([]);
  });

  it('catches a new status, polite or assertive region', () => {
    expect(grown({
      '/src/ui/New.tsx': '<p role="status">{line}</p>',
      '/src/ui/Polite.tsx': '{on && <p className="x" aria-live="polite">{line}</p>}',
      '/src/ui/Loud.tsx': "<div aria-live={busy ? 'off' : 'assertive'}>{line}</div>",
      '/src/ui/CopyButton.tsx': '<span role="status" /><span role={x ? \'alert\' : \'status\'} />',
    })).toEqual(['/src/ui/New.tsx: 1', '/src/ui/Polite.tsx: 1', '/src/ui/Loud.tsx: 1', '/src/ui/CopyButton.tsx: 2']);
  });

  it('counts an element once, and two elements twice', () => {
    expect(regions('<p className="a" role="status" aria-live="polite" onClick={() => go()}>{said}</p>')).toBe(1);
    expect(regions('<p aria-live="polite">{a}</p><p aria-live="polite">{b}</p>')).toBe(2);
    expect(regions('<p aria-live="off">{a}</p>')).toBe(0);
  });

  it('catches a listed one that went without the list going down', () => {
    expect(stale({ ...SOURCES, '/src/ui/CopyButton.tsx': '<span className="copy__note small">{note}</span>' }))
      .toEqual(['/src/ui/CopyButton.tsx: 0, listed 1']);
  });
});
