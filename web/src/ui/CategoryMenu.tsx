import { ChevronDown, Search } from 'lucide-react';
import { useEffect, useId, useLayoutEffect, useRef, useState, type KeyboardEvent } from 'react';
import { createPortal } from 'react-dom';
import { useUsual } from '../api/hooks';
import { CATEGORY_GROUPS, findCategories, label, UNSORTED } from '../lib/categories';
import { CAT_UNSURE, CATEGORY, FIND_CATEGORY, NO_CATEGORY_MATCH, TYPE_TO_FIND, YOUR_USUAL } from '../lib/copy';
import { composing } from '../lib/ime';
import { menuPlace, type MenuPlace } from '../lib/menuPlace';
import { anchoredStyle, anchorName, canAnchor, canPopover, invokerAttrs, usePopoverOpen } from '../lib/popover';
import { useWide } from '../lib/useWide';
import { Shown } from './Live';
import { Sheet } from './Sheet';
import './CategoryMenu.css';

/** Sorting redesign, Opening: a fine pointer and at least 900 px get the popover; everything else the half sheet. */
export const DESKTOP = '(pointer: fine) and (min-width: 900px)';

type Section = { id: string; name: string | null; keys: readonly string[] };

/** What the menu lists: with nothing typed, your usual, then the five groups (and Not sure yet where it is allowed);
 *  with something typed, one ranked list (findCategories). */
export function menuSections(q: string, usual: readonly string[], withUnsorted: boolean): Section[] {
  if (q.trim()) return [{ id: 'found', name: null, keys: findCategories(q, usual, withUnsorted) }];
  const out: Section[] = [];
  const mine = usual.filter((k) => k !== UNSORTED);
  if (mine.length) out.push({ id: 'usual', name: YOUR_USUAL, keys: mine });
  CATEGORY_GROUPS.forEach((g, i) => out.push({ id: `g${i}`, name: g.name, keys: g.keys }));
  if (withUnsorted) out.push({ id: 'unsure', name: CAT_UNSURE, keys: [UNSORTED] });
  return out;
}

/** One menu at a time (spec, The picker): a menu that opens closes the one before it. */
let closeOpen: (() => void) | null = null;

type MenuProps = {
  anchor: HTMLElement | null; value: string | null; title?: string; withUnsorted?: boolean;
  onPick: (key: string) => void; onClose: () => void;
};

/** Sorting redesign, The picker: an ARIA combobox (a search box and a listbox, aria-activedescendant). Mount it only
 *  while open. Desktop: a popover under or above the trigger, two columns of plain rows. Phone: a half sheet.
 *
 *  Issue #27: on a desktop it is a native popover shown from its trigger, which becomes the popover's invoker while
 *  it is open, so the browser closes it on a press or tap outside and never on the trigger itself; CSS anchor
 *  positioning keeps it by the trigger through resizing and zooming (CategoryMenu.css). Without anchor positioning it
 *  is placed once from the trigger's box (lib/menuPlace.ts); without popovers its own listener closes it. */
export function CategoryMenu({ anchor, value, title = CATEGORY, withUnsorted = false, onPick, onClose }: MenuProps) {
  const desktop = useWide(DESKTOP);
  const usual = useUsual().data?.keys ?? [];
  const [q, setQ] = useState('');
  const [active, setActive] = useState(0);
  const [place, setPlace] = useState<MenuPlace | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const [pop, setPop] = useState<HTMLDivElement | null>(null);
  const moved = useRef(false);
  const closer = useRef(onClose);
  closer.current = onClose;
  const id = useId();
  const popId = `${id}-pop`;
  const name = anchorName(id);
  const popover = desktop && canPopover();
  const anchored = desktop && canAnchor();
  const placed = desktop && (anchored || place !== null);
  usePopoverOpen(popover ? pop : null, { after: (now) => { if (!now) closer.current(); } });
  const sections = menuSections(q, usual, withUnsorted);
  const flat = sections.flatMap((s) => s.keys.map((k) => ({ key: k, id: `${id}-${s.id}-${k}` })));
  const at = Math.min(active, Math.max(0, flat.length - 1));
  const current = flat.length ? flat[at] : null;
  const activeId = current?.id;

  useEffect(() => {
    const mine = () => closer.current();
    closeOpen?.();
    closeOpen = mine;
    return () => { if (closeOpen === mine) closeOpen = null; };
  }, []);

  useLayoutEffect(() => {
    if (!desktop || anchored) return;
    const r = anchor?.getBoundingClientRect() ?? { left: 16, top: 16, bottom: 16 };
    setPlace(menuPlace(r, document.documentElement.clientWidth || window.innerWidth, window.innerHeight, window.scrollX, window.scrollY));
  }, [desktop, anchored, anchor]);

  // Shown from its trigger, which is its anchor and, while it is open, its invoker.
  useLayoutEffect(() => {
    if (!popover || !pop) return;
    const attrs = invokerAttrs(popId, 'show');
    if (anchor) {
      anchor.style.setProperty('anchor-name', name);
      for (const [k, v] of Object.entries(attrs)) anchor.setAttribute(k, v);
    }
    try { (pop.showPopover as (o?: { source?: HTMLElement }) => void)({ source: anchor ?? undefined }); } catch { pop.showPopover(); }
    return () => {
      if (!anchor) return;
      anchor.style.removeProperty('anchor-name');
      for (const k of Object.keys(attrs)) anchor.removeAttribute(k);
    };
  }, [popover, pop, anchor, popId, name]);

  // Once shown: a popover is shown in the commit after its element is known.
  useEffect(() => { if (placed && (!popover || pop)) input.current?.focus(); }, [placed, popover, pop]);

  useEffect(() => {
    if (!desktop || popover) return;
    const onDown = (e: PointerEvent) => {
      const t = e.target as Node;
      if (!pop?.contains(t) && !anchor?.contains(t)) closer.current();
    };
    document.addEventListener('pointerdown', onDown);
    return () => document.removeEventListener('pointerdown', onDown);
  }, [desktop, popover, pop, anchor]);

  // Only after the user moves the active row, and only once a desktop popover is placed: scrolling an unplaced
  // (hidden, end-of-body) popover would scroll the page and leave the popover off screen.
  useLayoutEffect(() => {
    if (!moved.current || !(desktop ? placed : true)) return;
    if (activeId) document.getElementById(activeId)?.scrollIntoView?.({ block: 'nearest' });
  }, [activeId, desktop, placed]);

  const leave = () => { anchor?.focus(); onClose(); };
  const pick = (k: string) => { anchor?.focus(); onPick(k); };
  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    const n = flat.length;
    if (e.key === 'ArrowDown' && n) { e.preventDefault(); moved.current = true; setActive((at + 1) % n); }
    else if (e.key === 'ArrowUp' && n) { e.preventDefault(); moved.current = true; setActive((at - 1 + n) % n); }
    else if (e.key === 'Enter') { e.preventDefault(); if (current && !composing(e)) pick(current.key); }
    else if (e.key === 'Escape' || e.key === 'Tab') { e.preventDefault(); e.stopPropagation(); leave(); }
  };

  const listId = `${id}-list`;
  const option = (s: Section, k: string) => {
    const oid = `${id}-${s.id}-${k}`;
    return (
      <div key={oid} id={oid} role="option" aria-selected={oid === activeId}
        className={`catmenu__opt${oid === activeId ? ' is-active' : ''}${k === value ? ' is-current' : ''}`}
        onMouseDown={(e) => { e.preventDefault(); }} onClick={() => { pick(k); }}>
        {label(k)}
      </div>
    );
  };
  const body = (
    <div className="catmenu">
      <label className="catmenu__search">
        <Search size={20} aria-hidden="true" />
        <input ref={input} type="text" role="combobox" value={q} placeholder={TYPE_TO_FIND} aria-label={FIND_CATEGORY}
          aria-expanded="true" aria-controls={listId} aria-autocomplete="list" aria-activedescendant={activeId}
          autoComplete="off" spellCheck={false} enterKeyHint="done"
          onChange={(e) => { moved.current = true; setQ(e.target.value); setActive(0); }} onKeyDown={onKey} />
      </label>
      {flat.length === 0 ? (
        <Shown className="catmenu__empty">{NO_CATEGORY_MATCH.replace('{q}', q.trim())}</Shown>
      ) : (
        <div id={listId} role="listbox" aria-label={title} className={`catmenu__list${q.trim() ? '' : ' catmenu__list--grouped'}`}>
          {sections.map((s) => (s.name === null ? s.keys.map((k) => option(s, k)) : (
            <div key={s.id} role="group" aria-labelledby={`${id}-${s.id}-h`} className={`catmenu__group${s.id === 'usual' ? ' catmenu__group--usual' : ''}`}>
              <div id={`${id}-${s.id}-h`} className="catmenu__head">{s.name}</div>
              {s.keys.map((k) => option(s, k))}
            </div>
          )))}
        </div>
      )}
    </div>
  );

  if (!desktop) {
    return <Sheet open onClose={onClose} title={title} focusRef={input} className="catmenu-sheet">{body}</Sheet>;
  }
  const style = anchored ? anchoredStyle(name)
    : place ? { left: place.left, top: place.top, width: place.width, height: place.height } : { visibility: 'hidden' as const };
  return createPortal(
    <div ref={setPop} id={popId} className="catmenu-pop" role="dialog" aria-label={title} style={style}
      {...(popover ? { popover: 'auto' as const } : {})}>
      {body}
    </div>,
    document.body,
  );
}

type FieldProps = { value: string; onChange: (key: string) => void; title?: string; disabled?: boolean; withUnsorted?: boolean };

/** A form field that looks like the other selects and opens the CategoryMenu (the transaction detail, Category rules). */
export function CategoryField({ value, onChange, title = CATEGORY, disabled = false, withUnsorted = true }: FieldProps) {
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const id = useId();
  return (
    <div className="field">
      <span className="field__label" id={`${id}-l`}>{title}</span>
      <button ref={trigger} type="button" className="catmenu__trigger" aria-haspopup="dialog" aria-expanded={open}
        aria-labelledby={`${id}-l ${id}-v`} disabled={disabled} onClick={() => { setOpen((o) => !o); }}>
        <span id={`${id}-v`}>{label(value)}</span>
        <ChevronDown size={20} aria-hidden="true" />
      </button>
      {open && (
        <CategoryMenu anchor={trigger.current} value={value} title={title} withUnsorted={withUnsorted}
          onPick={(k) => { setOpen(false); onChange(k); }} onClose={() => { setOpen(false); }} />
      )}
    </div>
  );
}
