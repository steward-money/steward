import { memo, Suspense, useRef, useState } from 'react';
import type { Txn } from '../../api/types';
import { aboutFor } from '../../ask/about';
import { useCanEdit } from '../../book/BookContext';
import { label } from '../../lib/categories';
import {
  BUSINESS, CATEGORY, EITHER_LINE, IGNORE, MORE_CATEGORIES, NOT_SAVED, NOTE_BUTTON, REASON_NO_RULE, REASON_P2P, REASON_UNKNOWN, REASON_WAITING,
  REIMBURSABLE, SKIP_FOR_NOW, UNDONE,
} from '../../lib/copy';
import { dateLabel, money } from '../../lib/format';
import { lazyScreen } from '../../lib/lazyScreen';
import { rich } from '../../lib/receipt';
import { UNDONE_MS, useHoldableTimer } from '../../lib/useHoldableTimer';
import { AskAbout } from '../../ui/AskAbout';
import { Button } from '../../ui/Button';
import { CategoryMenu } from '../../ui/CategoryMenu';
import { Shown } from '../../ui/Live';
import { Money } from '../../ui/Money';
import { Notice } from '../../ui/Notice';
import { Rich } from '../../ui/Rich';
import { TxnName, txnLabel } from '../../ui/TxnName';
import { NotesList } from '../transactions/NotesList';
import { GUESS_REASON, NONE_LIKE_IT } from './copy';
import { likeLine } from './toastText';
import { type ActKind, type Done, useReviewActions } from './useReviewActions';

const PayerInline = lazyScreen(() => import('../reimburse/PayerInline'));
const NoteForm = lazyScreen(() => import('../transactions/NoteForm'));

/** The page says once what Snooze does; each card's button points to that line. */
export const SNOOZE_HINT_ID = 'review-snooze-hint';

type Props = {
  txn: Txn; chips: readonly string[]; today: Date;
  /** The request is going out: Review takes a pending stack entry and hands back its id. */
  onBegin: (txn: Txn, kind: ActKind) => string;
  /** The reply landed: Review turns the card into a slim row in its place (P21) and shows the toast. */
  onDone: (txn: Txn, entry: string, d: Done) => void;
  onFail: (entry: string) => void;
  /** A note was saved or removed on the card: Review's stack and toast take it, so the toast outlives the form (Undo). */
  onNote: (txn: Txn, token: string, text: string) => void;
  /** How many times Undo grew this card back: from 1, it says "Undone." for 2 seconds. */
  back?: number;
  /** Bulk sort: selection mode is on, so a click anywhere on the card picks it (Shift: a range). */
  selecting?: boolean;
  picked?: boolean;
  onPick?: (id: number, range: boolean) => void;
};

/** Why the row is here (phase 12 decision 8; review focus P5), from the API's reason. Older replies have none. */
export function reasonLine(txn: Txn): string | null {
  switch (txn.reason) {
    case 'p2p': return REASON_P2P.replace('{app}', txn.merchant ?? '');
    case 'unsure': return GUESS_REASON;
    case 'unknown': return REASON_UNKNOWN;
    case 'waiting': return REASON_WAITING;
    case 'no_rule': return REASON_NO_RULE;
    default: return null;
  }
}

/** A card in Review's list (review focus: the receipt is gone, P34). Asked up front: the "Also" box for the rows like
 *  it, the chips with the guess first, Business and Reimbursable, Note, Snooze and Ignore. When the reply lands Review
 *  swaps it for a slim row; PR 2's Note keeps the card. */
function ReviewCardView({ txn, chips, today, onBegin, onDone, onFail, onNote, back = 0, selecting = false, picked = false, onPick }: Props) {
  const canEdit = useCanEdit();
  const entry = useRef<string | null>(null);
  const a = useReviewActions(txn, {
    onStart: (kind) => { entry.current = onBegin(txn, kind); },
    onDone: (d) => { const e = entry.current; entry.current = null; if (e) onDone(txn, e, d); },
    onFail: () => { const e = entry.current; entry.current = null; if (e) onFail(e); },
  });
  const [menu, setMenu] = useState(false);
  const [noting, setNoting] = useState(false);
  // The card's notes, asked for only when it has some or one was saved from it (no request for the rest).
  const [noted, setNoted] = useState(false);
  const noteBtn = useRef<HTMLButtonElement>(null);
  const cardEl = useRef<HTMLElement>(null);   // where focus goes after Remove when there is no Note (a parent book)
  const [undone, setUndone] = useState<number | null>(back > 0 ? back : null);
  useHoldableTimer(undone, UNDONE_MS, false, () => { setUndone(null); });
  const more = useRef<HTMLButtonElement>(null);
  const guess = txn.guess ?? null;
  const rest = chips.filter((c) => c !== guess);
  const reason = reasonLine(txn);
  const locked = a.busy;
  const chip = (c: string, extra = '') => (
    <button key={c} type="button" className={`chip${extra}${a.chosen === c ? ' is-on' : ''}`} aria-pressed={a.chosen === c}
      disabled={locked} onClick={() => { a.sort(c); }}>
      {label(c)}
    </button>
  );

  return (
    <article ref={cardEl} tabIndex={-1} className={`card rcard${selecting ? ` is-picking is-compact${picked ? ' is-picked' : ''}` : ''}`}
      data-testid="review-card" data-id={txn.id} data-busy={a.busy || undefined}
      onClick={selecting ? (e) => { onPick?.(txn.id, e.shiftKey); } : undefined}>
      <div className="rcard__top">
        {selecting && (
          <input type="checkbox" className="pick-box rcard__pick" checked={picked} aria-label={`${txnLabel(txn)}, ${money(txn.amount_cents)}`}
            onClick={(e) => { e.stopPropagation(); }} onChange={(e) => { onPick?.(txn.id, (e.nativeEvent as MouseEvent).shiftKey === true); }} />
        )}
        <span className="rcard__desc"><TxnName t={txn} /></span>
        <Money cents={txn.amount_cents} size="l" />
        <span className="rcard__meta">{dateLabel(txn.posted_at, today)} · {txn.account_name}</span>
        {reason && !selecting && <span className="rcard__reason">{reason}</span>}
      </div>
      {!selecting && (
        <div className="rcard__body">
          {undone !== null && <Shown className="rcard__undone">{UNDONE}</Shown>}
          {a.matching !== null && (a.matching > 0 ? (
            <label className="check-row rcard__also">
              <input type="checkbox" checked={a.also} disabled={locked} onChange={(e) => { a.setAlso(e.target.checked); }} />
              <span>{likeLine(a.matching)}</span>
            </label>
          ) : <p className="rcard__nomatch muted">{NONE_LIKE_IT}</p>)}
          <div className="chips" role="group" aria-label={CATEGORY}>
            {guess && chip(guess, ' chip--guess')}
            {rest.map((c) => chip(c))}
            <button ref={more} type="button" className="chip chip--more" aria-haspopup="dialog" aria-expanded={menu}
              disabled={locked} onClick={() => { setMenu((m) => !m); }}>{MORE_CATEGORIES}</button>
          </div>
          {menu && (
            <CategoryMenu anchor={more.current} value={a.chosen} onClose={() => { setMenu(false); }}
              onPick={(k) => { setMenu(false); a.sort(k); }} />
          )}
          {((txn.note_count ?? 0) > 0 || noted) && (
            <NotesList txn={txn} today={today} onCard onRemoved={(token, text) => { onNote(txn, token, text); }}
              home={() => { (noteBtn.current ?? cardEl.current)?.focus(); }} />
          )}
          {noting && a.household && canEdit && (
            <Suspense fallback={null}>
              <div className="rcard__note">
                <NoteForm txn={txn} onCancel={() => { setNoting(false); }}
                  onSaved={(s) => { setNoting(false); setNoted(true); onNote(txn, s.token, s.text); noteBtn.current?.focus(); }} />
              </div>
            </Suspense>
          )}
          {a.error && <Notice tone="error"><Rich parts={rich(NOT_SAVED, { message: a.error })} /></Notice>}
          <div className="rcard__foot">
            <div className="rcard__switches">
              {a.household && (
                <button type="button" role="switch" aria-checked={a.business} className={`rev-switch${a.business ? ' is-on' : ''}`} onClick={a.toggleBusiness}>
                  <span className="rev-switch__track" aria-hidden="true" />{BUSINESS}
                </button>
              )}
              {a.canReimburse && (
                <button type="button" role="switch" aria-checked={a.reimb} className={`rev-switch${a.reimb ? ' is-on' : ''}`} disabled={locked} onClick={a.toggleReimb}>
                  <span className="rev-switch__track" aria-hidden="true" />{REIMBURSABLE}
                </button>
              )}
            </div>
            <div className="rcard__actions">
              {a.household && canEdit && (
                <Button ref={noteBtn} variant="quiet" aria-expanded={noting} disabled={locked} onClick={() => { setNoting((n) => !n); }}>{NOTE_BUTTON}</Button>
              )}
              <Button variant="quiet" aria-describedby={SNOOZE_HINT_ID} disabled={locked} onClick={() => { a.post('snooze'); }}>{SKIP_FOR_NOW}</Button>
              <Button variant="quiet" disabled={locked} onClick={() => { a.post('ignore'); }}>{IGNORE}</Button>
              <AskAbout about={aboutFor.transaction(txn)} surface={{ kind: 'transaction', category: txn.category }} thing={txnLabel(txn)} />
            </div>
          </div>
          {a.either && <Shown className="rcard__either muted">{EITHER_LINE}</Shown>}
          {a.asking && (
            <Suspense fallback={null}>
              <PayerInline payers={a.payers} value={a.payerId} onChoose={a.choosePayer} />
            </Suspense>
          )}
        </div>
      )}
    </article>
  );
}

/** Memoized: acting on one card re-renders no other (Performance 4). Review's callbacks are stable. */
export const ReviewCard = memo(ReviewCardView);
