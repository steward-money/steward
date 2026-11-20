import { createContext, createElement, useCallback, useContext, useEffect, useMemo, useRef, useState, type HTMLAttributes, type ReactNode, type RefObject } from 'react';
import './Live.css';

type Live = {
  /** A polite live region that was in the page before any toast: UndoToast renders into it, so the toast is an addition
   *  to a live region a screen reader already knows, which it reads. */
  toasts: HTMLElement | null;
  /** Says a line through a polite live region that was in the page before the line (a route's name, a field's error,
   *  a notice that has appeared). */
  say: (text: string) => void;
};

const LiveCtx = createContext<Live | null>(null);

/** The nearest live regions: the page's (RouteFrame), or an open sheet's own, since a modal sheet makes the page behind
 *  it inert and a region there would go unheard. Null outside both (a component test). */
export const useLive = () => useContext(LiveCtx);

/** WEB-7: a live region added with its text already in it is often not read. These two are in the page from the start
 *  and only their contents change. */
export function LiveRegions({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<HTMLElement | null>(null);
  const region = useRef<HTMLDivElement>(null);
  const last = useRef({ text: '', alt: false });
  // Lines said in the same moment (a page change says its name as a notice on the new page appears) are said
  // together: each would otherwise replace the one before it unheard.
  const together = useRef<string[] | null>(null);
  const say = useCallback((line: string) => {
    const el = region.current;
    if (!el || !line) return;
    if (together.current) { if (!together.current.includes(line)) together.current.push(line); }
    else {
      together.current = [line];
      queueMicrotask(() => { together.current = null; });
    }
    const text = together.current.join(' ');
    // VoiceOver skips a line the same as the one before; a trailing no-break space makes it differ and isn't read.
    const alt = text === last.current.text ? !last.current.alt : false;
    last.current = { text, alt };
    el.textContent = alt ? `${text} ` : text;
  }, []);
  const value = useMemo(() => ({ toasts, say }), [toasts, say]);
  return (
    <LiveCtx.Provider value={value}>
      {children}
      <div ref={setToasts} aria-live="polite" className="live-toasts" data-testid="live-toasts" />
      <div ref={region} aria-live="polite" className="live-said" data-testid="live-said" />
    </LiveCtx.Provider>
  );
}

/** The words a screen reader would read in `el`: its text, without what is hidden from it, in one line. */
export function heard(el: Element | null): string {
  if (!el) return '';
  const parts: string[] = [];
  const walk = (n: Node) => {
    if (n.nodeType === Node.TEXT_NODE) { parts.push(n.textContent ?? ''); return; }
    if (n instanceof Element && (n.getAttribute('aria-hidden') === 'true' || n.hasAttribute('hidden'))) return;
    // An alert inside (a save that failed, beside a readout) is read by itself; it isn't said a second time.
    if (n instanceof Element && n !== el && n.getAttribute('role') === 'alert') return;
    // A line break, and a part laid out on its own (a paragraph, a label set as a block), part its words from what is
    // on either side: "Accounts without a type" and the line under it are not run together. Inline parts (a bold name)
    // join the words around them.
    if (n instanceof HTMLBRElement) { parts.push(' '); return; }
    const own = n instanceof HTMLElement && n !== el && !getComputedStyle(n).display.startsWith('inline');
    if (own) parts.push(' ');
    for (const c of n.childNodes) walk(c);
    if (own) parts.push(' ');
  };
  walk(el);
  return parts.join('').replace(/\s+/g, ' ').trim();
}

/** Issue #23: says what `el` shows through the polite region when it appears and whenever its words change, so a
 *  visible line (a notice, Review's caught-up state) is heard without being a live region added with its text.
 *  `quietOnMount`, read when the hook first runs, takes the first words as already said: only later changes are said. */
export function useSayShown(el: RefObject<HTMLElement | null>, on = true, quietOnMount = false) {
  const live = useLive();
  const said = useRef('');
  const quiet = useRef(quietOnMount);
  // The text last looked at. This runs after every render and heard() reads each part's style, so heard() runs only
  // when the text has changed.
  const seen = useRef('');
  useEffect(() => {
    if (!on || !live) return;
    const node = el.current;
    const raw = node?.textContent ?? '';
    if (raw === seen.current) return;
    const text = heard(node);
    if (quiet.current) {
      quiet.current = false;
      seen.current = raw;
      said.current = text;
      return;
    }
    // Inside a part hidden from screen readers it isn't said yet. The text isn't marked seen, so it is said when this
    // runs again after the part is shown.
    if (node?.closest(HIDDEN)) return;
    seen.current = raw;
    if (!text || text === said.current) return;
    said.current = text;
    live.say(text);
  });
}

/** A part hidden from screen readers: a line inside one is not said. */
const HIDDEN = '[aria-hidden="true"],[inert],[hidden]';

/** True while a component paints for the first time, false on every render after. A line given
 *  `quietOnMount={useArriving()}` is quiet when it shows with the screen (what was already saved) and said when it
 *  appears later (a change the person made, or a line that hid and came back). */
export function useArriving(): boolean {
  const arriving = useRef(true);
  useEffect(() => { arriving.current = false; }, []);
  return arriving.current;
}

type ShownProps = HTMLAttributes<HTMLElement> & { as?: 'p' | 'span' | 'div'; ref?: RefObject<HTMLElement | null>; quietOnMount?: boolean };

/** Issue #51: a line that appears with its words already in it (Saved, a check's result, a cheer). It is not a live
 *  region of its own, since one added with its text is often not read; its words are said through the page's polite
 *  region when it appears and whenever they change, and again each time it appears. `ref`, when given, is the
 *  element's (a moment that plays on the line). `quietOnMount` is read when the line mounts: its first words are taken
 *  as already said, so a line showing saved state is quiet when the page opens. A line that mounts again with it is
 *  quiet again; pass useArriving() so that only the lines on the screen's first paint are quiet. */
export function Shown({ as = 'p', ref, quietOnMount = false, ...props }: ShownProps) {
  const own = useRef<HTMLElement>(null);
  const el = ref ?? own;
  useSayShown(el, true, quietOnMount);
  return createElement(as, { ...props, ref: el });
}
