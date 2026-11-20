import { Suspense, useEffect, useId, useLayoutEffect, useRef, useState, type MutableRefObject } from 'react';
import type { Txn } from '../../../api/types';
import { aboutFor } from '../../../ask/about';
import { openAsk } from '../../../ask/store';
import { useBook, useCanEdit } from '../../../book/BookContext';
import { label } from '../../../lib/categories';
import { celebrationsOn, reducedMotion, soundOn, withCelebrate } from '../../../lib/celebrations';
import { ALREADY_SORTED, ASK_ABOUT_THIS, BUSINESS, CATEGORY, EITHER_LINE, IGNORE, MORE_CATEGORIES, NOT_SAVED, REIMBURSABLE, UNDONE } from '../../../lib/copy';
import { dateLabel } from '../../../lib/format';
import { lazyScreen } from '../../../lib/lazyScreen';
import { rich } from '../../../lib/receipt';
import { UNDONE_MS, useHoldableTimer } from '../../../lib/useHoldableTimer';
import { CategoryMenu } from '../../../ui/CategoryMenu';
import { Shown } from '../../../ui/Live';
import { Money } from '../../../ui/Money';
import { Notice } from '../../../ui/Notice';
import { Rich } from '../../../ui/Rich';
import { TxnName } from '../../../ui/TxnName';
import { NotesList } from '../../transactions/NotesList';
import { NONE_LIKE_IT } from '../copy';
import { ignoreKey } from '../keys';
import { reasonLine } from '../ReviewCard';
import { likeLine } from '../toastText';
import { type ActKind, type Done, useReviewActions } from '../useReviewActions';
import * as C from './copy';
import { chipsFor, keyAct } from './keymap';
import { MoreMenu } from './MoreMenu';
import { LEAVE_MS, leaveFrames, type Leave, play, STAMP_LANDS_MS } from './motion';
import type { Dir } from './swipe';
import { useDemo } from './useDemo';
import { useSwipe } from './useSwipe';

const PayerInline = lazyScreen(() => import('../../reimburse/PayerInline'));
const NoteForm = lazyScreen(() => import('../../transactions/NoteForm'));

export type CardRole = 'current' | 'next' | 'leaving';
/** What the persistent buttons under the stage call on the current card; true when a request went out. */
export type Controls = { take: () => boolean; snooze: () => boolean };

type Props = {
  txn: Txn; chips: readonly string[]; today: Date; role: CardRole;
  leave: Leave | null;
  /** Swipe to sort is on (P2). */
  swipe: boolean;
  /** P13: this is the first focus card and the nudge should play. */
  demo: boolean;
  /** P32: the current card's row was sorted elsewhere. */
  elsewhere: boolean;
  /** Undo brought this card back: "Undone." for 2 seconds. A new number each time. */
  undone: number | null;
  /** An action is in flight in focus: buttons and keys wait (P8). */
  busy: boolean;
  /** A fine pointer that hovers: chip numbers and aria-keyshortcuts (P24). */
  fine: boolean;
  controls: MutableRefObject<Controls | null>;
  onBegin: (txn: Txn, kind: ActKind) => string;
  onDone: (txn: Txn, entry: string, d: Done, how: Leave) => void;
  onFail: (entry: string) => void;
  onNote: (txn: Txn, token: string, text: string) => void;
  onNext: () => void;
  onLeft: (id: number) => void;
};

const HOW: Record<Done['kind'], Leave> = { sort: 'right', snooze: 'left', ignore: 'drop' };

/** One card in focus (review focus, Focus: one card). The current card takes actions; the next sits behind it, inert;
 *  the card just acted on stays on top while it leaves (engineering choice 1). Keyed by id in one list, so the same
 *  element is current, then leaving: its stamp and a flown-out transform (data-flown) stay with it. */
export function FocusCard({ txn, chips, today, role, leave, swipe, demo, elsewhere, undone, busy, fine, controls, onBegin, onDone, onFail, onNote, onNext, onLeft }: Props) {
  const card = useRef<HTMLElement>(null);
  const more = useRef<HTMLButtonElement>(null);
  const entry = useRef<string | null>(null);
  const fx = useRef<(() => void) | null>(null);
  const stampedAt = useRef<number | null>(null);
  const edge = useRef<Record<Dir, HTMLSpanElement | null>>({ right: null, left: null, up: null });
  const swiped = useRef(false);          // a swipe ticked at its threshold: the stamp's landing tick is skipped (P33)
  const [menu, setMenu] = useState(false);
  const canEdit = useCanEdit();
  const chat = useBook()?.chat === true;
  const [moreOpen, setMoreOpen] = useState(false);
  const [noting, setNoting] = useState(false);
  const [opened, setOpened] = useState(false);     // once opened the form stays (hidden), so a draft survives Esc
  const [noted, setNoted] = useState(false);       // a note was saved here: show the card's notes (asked for only then, or with some)
  const [showUndone, setShowUndone] = useState<number | null>(undone);
  useEffect(() => { if (undone !== null) setShowUndone(undone); }, [undone]);
  useHoldableTimer(showUndone, UNDONE_MS, false, () => { setShowUndone(null); });
  const titleId = useId();
  const metaId = useId();
  const amountId = useId();
  const guessId = useId();
  const current = role === 'current';
  const guess = txn.guess ?? null;
  const shown = chipsFor(guess, chips);
  const reason = reasonLine(txn);

  // The stamp starts at once (P8), with celebrations on (never under reduced motion, P19).
  function stamp(kind: ActKind, category: string | null) {
    const el = card.current;
    if (!el || !celebrationsOn()) return;
    stampedAt.current = Date.now();
    const applied = a.also && (a.matching ?? 0) > 0 ? a.matching ?? 0 : 0;
    const tick = !swiped.current;
    void withCelebrate((c) => {
      fx.current = kind === 'sort'
        ? c.sorted({ card: el, category: category ?? '', applied, sound: soundOn(), tick })
        : c.quiet({ card: el, kind });
    });
  }

  const a = useReviewActions(txn, {
    onStart: (kind, category) => { entry.current = onBegin(txn, kind); stamp(kind, category); },
    onDone: (d) => { const e = entry.current ?? ''; entry.current = null; swiped.current = false; onDone(txn, e, d, HOW[d.kind]); },
    onFail: () => {
      fx.current?.();
      fx.current = null;
      stampedAt.current = null;
      swiped.current = false;
      const e = entry.current;
      entry.current = null;
      if (e) onFail(e);
      sw.back();
    },
  });

  /** The right button, Enter and a right swipe: take the guess; without one, All categories (P4); sorted elsewhere,
   *  Next (P32). True when a request went out. */
  function take(): boolean {
    if (elsewhere) { onNext(); return false; }
    if (busy || a.busy) return false;
    if (!guess) { setMenu(true); return false; }
    return a.sort(guess);
  }
  function snooze(): boolean { return !elsewhere && !busy && a.post('snooze'); }
  useLayoutEffect(() => { if (current) controls.current = { take, snooze }; });

  // The note box (P27): N or Add note opens it with focus in Note; saving or Esc closes it and focus returns to the
  // card. Saving never moves the card on.
  const openNote = () => { setOpened(true); setNoting(true); };
  const closeNote = () => { setNoting(false); card.current?.focus(); };

  // Review focus, Keyboard: one document listener, on the current card only. ignoreKey drops keys typed into a field, in
  // a dialog or menu, with a modifier, held, already handled, or while an action is in flight.
  const onKey = useRef<(e: KeyboardEvent) => void>(() => {});
  onKey.current = (e: KeyboardEvent) => {
    if (ignoreKey(e, { busy: busy || a.busy, menuOpen: menu || moreOpen })) return;
    const k = keyAct(e.key, shown.length);
    if (!k) return;
    if (k.type === 'take') { e.preventDefault(); take(); return; }
    if (elsewhere) return;
    switch (k.type) {
      case 'chip': e.preventDefault(); a.sort(shown[k.index]); break;
      case 'more': e.preventDefault(); setMenu(true); break;       // "/" never lands in the menu's search
      case 'snooze': snooze(); break;
      case 'ignore': a.post('ignore'); break;
      case 'business': if (a.household) a.toggleBusiness(); break;
      case 'note': if (a.household && canEdit) { e.preventDefault(); openNote(); } break;
    }
  };
  useEffect(() => {
    if (!current) return;
    const listen = (e: KeyboardEvent) => { onKey.current(e); };
    document.addEventListener('keydown', listen);
    return () => { document.removeEventListener('keydown', listen); };
  }, [current]);
  const items = [
    { key: 'ignore', label: IGNORE, onSelect: () => { a.post('ignore'); } },
    ...(a.household ? [{ key: 'business', label: BUSINESS, checked: a.business, onSelect: a.toggleBusiness }] : []),
    ...(a.canReimburse ? [{ key: 'reimb', label: REIMBURSABLE, checked: a.reimb, onSelect: a.toggleReimb }] : []),
    ...(a.household && canEdit ? [{ key: 'note', label: C.ADD_NOTE, onSelect: openNote }] : []),
    // Ask about this (P19): no key; the menu has given focus back to ⋯, which is where focus returns when the panel closes.
    ...(chat ? [{ key: 'ask', label: ASK_ABOUT_THIS, onSelect: () => {
      openAsk(aboutFor.transaction(txn), { kind: 'transaction', category: txn.category }, document.activeElement as HTMLElement | null);
    } }] : []),
  ];

  // Right takes the guess, left snoozes for a week, up opens all categories (decision 4). A right swipe that can't sort
  // springs back and does the button's thing (engineering choice 21).
  const sw = useSwipe(card, edge, {
    enabled: swipe && current && !elsewhere,
    reduced: reducedMotion(),
    onCommit: (dir, ticked) => {
      if (dir === 'up') { setMenu(true); return 'back'; }
      swiped.current = ticked;
      const went = dir === 'right' ? take() : snooze();
      if (!went) swiped.current = false;
      return went ? 'fly' : 'back';
    },
  });
  const playing = useDemo(demo && current && swipe, card, edge);
  const edgeLabel = (d: Dir) => (d === 'left' ? C.EDGE_SNOOZE : d === 'up' || !guess ? C.ALL_CATEGORIES : C.EDGE_TAKE.replace('{category}', label(guess)));

  // Leaving: the stamp lands first (when one started), then the card slides out; a card a swipe flew out is already
  // gone; reduced motion just swaps (P10).
  useLayoutEffect(() => {
    if (role !== 'leaving') return;
    const el = card.current;
    let cancelled = false;
    const go = () => {
      if (cancelled) return;
      if (!el || reducedMotion()) { onLeft(txn.id); return; }
      if (el.dataset.flown) {              // a swipe flew it out: wait off-screen for that flight to finish (220 ms)
        const flying = el.getAnimations?.() ?? [];
        if (!flying.length) { onLeft(txn.id); return; }
        void Promise.all(flying.map((f) => f.finished)).then(() => { if (!cancelled) onLeft(txn.id); }, () => { onLeft(txn.id); });
        return;
      }
      const anim = play(el, leaveFrames(leave ?? 'right'), { duration: LEAVE_MS, easing: 'ease-in', fill: 'forwards' });
      if (!anim) { onLeft(txn.id); return; }
      void anim.finished.then(() => { onLeft(txn.id); }, () => {});
    };
    const wait = stampedAt.current === null ? 0 : Math.max(0, STAMP_LANDS_MS - (Date.now() - stampedAt.current));
    const t = wait > 0 ? window.setTimeout(go, wait) : 0;
    if (wait <= 0) go();
    return () => { cancelled = true; window.clearTimeout(t); };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only becoming the leaving card starts the exit
  }, [role]);

  // Undone while it was still leaving: the same element is current again, so its stamp, its flight and its flown-out
  // transform go, and it rests where a card rests.
  const was = useRef(role);
  useLayoutEffect(() => {
    const el = card.current;
    if (was.current === 'leaving' && role === 'current' && el) {
      fx.current?.();
      fx.current = null;
      stampedAt.current = null;
      swiped.current = false;
      delete el.dataset.flown;
      for (const f of el.getAnimations?.() ?? []) f.cancel();
    }
    was.current = role;
  }, [role]);

  return (
    <article ref={card} tabIndex={current ? -1 : undefined}
      className={`card fcard fcard--${role}${swipe && current ? ' is-swipeable' : ''}${elsewhere ? ' is-elsewhere' : ''}`}
      data-testid="focus-card" data-demo={playing ? 'playing' : undefined} data-id={txn.id} data-role={role} data-busy={a.busy || undefined}
      aria-labelledby={titleId} aria-describedby={`${amountId} ${metaId} ${guessId}`} aria-hidden={current ? undefined : true} inert={!current} {...(current ? sw.handlers : {})}>
      {swipe && current && (['right', 'left', 'up'] as const).map((d) => (
        <span key={d} ref={(el) => { edge.current[d] = el; }} className={`fcard__edge fcard__edge--${d}`} aria-hidden="true">{edgeLabel(d)}</span>
      ))}
      <div className="fcard__top">
        <span id={titleId} className="rcard__desc"><TxnName t={txn} /></span>
        <span id={amountId}><Money cents={txn.amount_cents} size="l" /></span>
        {current && !elsewhere && <MoreMenu items={items} onOpen={setMoreOpen} />}
      </div>
      <p id={metaId} className="rcard__meta fcard__meta">{dateLabel(txn.posted_at, today)} · {txn.account_name}</p>
      {(a.business || a.reimb) && (
        <p className="fcard__tags">
          {a.business && <span className="tag">{BUSINESS}</span>}
          {a.reimb && <span className="tag">{REIMBURSABLE}</span>}
        </p>
      )}
      <div className="fcard__body" inert={elsewhere}>
        {showUndone !== null && <Shown className="rcard__undone">{UNDONE}</Shown>}
        <div id={guessId} className={`fcard__guess${guess ? '' : ' is-plain'}`}>
          {guess && <p className="fcard__probably"><Rich parts={rich(C.PROBABLY, { category: label(guess) })} /></p>}
          {reason && <p className="fcard__reason">{reason}</p>}
        </div>
        {a.error && <Notice tone="error"><Rich parts={rich(NOT_SAVED, { message: a.error })} /></Notice>}
        <div className="chips" role="group" aria-label={CATEGORY}>
          {shown.map((c, i) => (
            <button key={c} type="button" className={`chip${c === guess ? ' chip--guess' : ''}${a.chosen === c ? ' is-on' : ''}`}
              aria-pressed={a.chosen === c} aria-keyshortcuts={fine ? String(i + 1) : undefined} disabled={a.busy}
              onClick={() => { a.sort(c); }}>
              {fine && <kbd aria-hidden="true">{i + 1}</kbd>}{label(c)}
            </button>
          ))}
          <button ref={more} type="button" className="chip chip--more" aria-haspopup="dialog" aria-expanded={menu}
            aria-keyshortcuts={fine ? '/' : undefined} disabled={a.busy} onClick={() => { setMenu((m) => !m); }}>
            {fine && <kbd aria-hidden="true">/</kbd>}{MORE_CATEGORIES}
          </button>
        </div>
        {menu && current && (
          <CategoryMenu anchor={more.current} value={a.chosen} onClose={() => { setMenu(false); }}
            onPick={(k) => { setMenu(false); a.sort(k); }} />
        )}
        {((txn.note_count ?? 0) > 0 || noted) && (
          <NotesList txn={txn} today={today} onCard onRemoved={(token, text) => { onNote(txn, token, text); }}
            home={() => { card.current?.focus(); }} />
        )}
        {opened && current && (
          <div className="rcard__note fcard__note" hidden={!noting}>
            <Suspense fallback={null}>
              <NoteForm txn={txn} autoFocus={noting} onCancel={closeNote}
                onSaved={(s) => { setNoting(false); setNoted(true); onNote(txn, s.token, s.text); card.current?.focus(); }} />
            </Suspense>
            {fine && <p className="fcard__notekeys muted" aria-hidden="true">{C.NOTE_KEYS}</p>}
          </div>
        )}
        {a.matching !== null && (a.matching > 0 ? (
          <label className="check-row rcard__also">
            <input type="checkbox" checked={a.also} disabled={a.busy} onChange={(e) => { a.setAlso(e.target.checked); }} />
            <span>{likeLine(a.matching)}</span>
          </label>
        ) : <p className="rcard__nomatch muted">{NONE_LIKE_IT}</p>)}
        {a.either && <Shown className="rcard__either muted">{EITHER_LINE}</Shown>}
        {a.asking && current && (
          <Suspense fallback={null}>
            <PayerInline payers={a.payers} value={a.payerId} onChoose={a.choosePayer} />
          </Suspense>
        )}
      </div>
      {elsewhere && <p className="rcard__settled fcard__elsewhere">{ALREADY_SORTED}</p>}
    </article>
  );
}
