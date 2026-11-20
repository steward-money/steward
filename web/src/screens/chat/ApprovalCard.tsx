// The store-names card (spec 4.4; JR decisions 2 and 6). A manager sees why Claude asks, the names that would go with
// how many rows each appears in, every row as it would go and which real account each alias is; then
// "Approve for this conversation" (approved rows go with its later questions) or "Don't send". A viewer's card has no buttons.
// A row whose bank line reads as Zelle, a wire, a transfer or a payment app goes as "Zelle to [name]" or "Transfer from
// [name]"; the row's "Send this name too" box, which shows the name, swaps it in (public issue #1). The rows and the names
// above them show what would go, except that after the OK the gateway replaces a person's name its model finds in a row
// the manager didn't tick with "[name]" (public issue #53).
import { Check, Store, X } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import { label as categoryLabel } from '../../lib/categories';
import { money } from '../../lib/format';
import { Button } from '../../ui/Button';
import { Notice } from '../../ui/Notice';
import { andList, answerCard, fill, orList, type Card } from './askApi';
import * as C from './copy';

const rowsWord = (n: number) => (n === 1 ? '1 row' : `${n} rows`);

/** The names that would go with how many rows and payees carry each, most first, as the server counts them. */
function namesGoing(going: string[]): { name: string; count: number }[] {
  const counts = new Map<string, number>();
  for (const n of going) counts.set(n, (counts.get(n) ?? 0) + 1);
  return [...counts].map(([name, count]) => ({ name, count })).sort((a, b) => b.count - a.count);
}

function SendName({ full, on, toggle }: { full: string; on: boolean; toggle: () => void }): ReactNode {
  return (
    <label className="approve__send">
      <input type="checkbox" className="check" checked={on} onChange={toggle} />
      <span>{C.CARD_SEND_NAME} <span className="muted">({full})</span></span>
    </label>
  );
}

export function ApprovalCard({ turnId, card }: { turnId: string; card: Card }) {
  const [busy, setBusy] = useState(false);
  const [answered, setAnswered] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ticked, setTicked] = useState<ReadonlySet<string>>(new Set());
  const toggle = (key: string) => setTicked((t) => { const n = new Set(t); if (n.has(key)) n.delete(key); else n.add(key); return n; });
  const rowGoes = (i: number) => { const r = card.rows[i]; return r.full && ticked.has(`r${i}`) ? r.full : r.store; };
  const payeeGoes = (i: number) => { const p = card.payees[i]; return p.full && ticked.has(`p${i}`) ? p.full : p.name; };
  const names = namesGoing([...card.rows.map((_, i) => rowGoes(i)), ...card.payees.map((_, i) => payeeGoes(i))]);
  async function answer(send: boolean) {
    setBusy(true);
    setError(null);
    try {
      await answerCard(turnId, card.call_id, send, card.hash, send ? [...ticked].sort() : []);
      setAnswered(true);                    // the stream's approved or declined event replaces this card
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const ask = card.rows.length ? fill(C.CARD_ROWS_ASK, { rows: rowsWord(card.rows.length), first: card.first ?? '', last: card.last ?? '' }) : C.CARD_PAYEES_ASK;
  const pairs = andList(card.accounts.map((a) => `${a.alias} is ${a.label}`));
  return (
    <div className="card approve" role="group" aria-labelledby={`card-${card.call_id}`}>
      <div className="approve__head">
        <Store size={20} aria-hidden="true" />
        <div className="stack stack--tight">
          <span id={`card-${card.call_id}`} className="approve__title">{C.CARD_TITLE}</span>
          <span>{card.reason ? `${card.reason} ${ask}` : ask}</span>
        </div>
      </div>
      <div className="stack stack--tight">
        <span className="eyebrow">{C.CARD_NAMES}</span>
        <div className="names">{names.map((n) => <span key={n.name} className="name">{n.name} <small>{n.count}</small></span>)}</div>
      </div>
      {card.rows.length > 0 && (
        <div className="stack stack--tight">
          <span className="eyebrow">{C.CARD_EVERY_ROW}</span>
          <table className="rows">
            <thead><tr><th>{C.COL_DATE}</th><th>{C.COL_STORE}</th><th className="num">{C.COL_AMOUNT}</th><th>{C.COL_CATEGORY}</th><th>{C.COL_ACCOUNT}</th></tr></thead>
            <tbody>
              {card.rows.map((r, i) => (
                <tr key={i}><td>{r.date}</td><td><span className="strong">{rowGoes(i)}</span>{r.full && <SendName full={r.full} on={ticked.has(`r${i}`)} toggle={() => toggle(`r${i}`)} />}</td><td className="num">{money(Math.abs(r.amount_cents))}</td><td>{categoryLabel(r.category)}</td><td>{r.account}</td></tr>
              ))}
            </tbody>
          </table>
          {card.more && <p className="small muted">{C.CARD_MORE}</p>}
          {pairs && <p className="small muted">{fill(C.CARD_ACCOUNTS, { pairs })}</p>}
        </div>
      )}
      {card.payees.length > 0 && (
        <ul className="plain small">{card.payees.map((p, i) => (
          <li key={p.payee}>{p.payee}: <span className="strong">{payeeGoes(i)}</span>{p.full && <SendName full={p.full} on={ticked.has(`p${i}`)} toggle={() => toggle(`p${i}`)} />}</li>
        ))}</ul>
      )}
      {error && <Notice tone="error">{error}</Notice>}
      <div className="approve__acts">
        <Button icon={Check} busy={busy} disabled={answered} onClick={() => void answer(true)}>{C.APPROVE}</Button>
        <Button variant="quiet" icon={X} disabled={busy || answered} onClick={() => void answer(false)}>{C.DONT_SEND}</Button>
      </div>
      <p className="small muted">{fill(C.CARD_FOOT, { time: card.until })}</p>
    </div>
  );
}

export function ViewerCard({ managers, where }: { managers: string[]; where: string }) {
  return (
    <div className="card approve approve--viewer" role="group" aria-label={C.VIEWER_CARD_TITLE}>
      <div className="approve__head">
        <Store size={20} aria-hidden="true" />
        <div className="stack stack--tight">
          <span className="approve__title">{C.VIEWER_CARD_TITLE}</span>
          <span>{C.VIEWER_CARD_BODY}</span>
          {managers.length > 0 && <span className="small muted">{fill(C.VIEWER_CARD_WHO, { managers: orList(managers), where })}</span>}
        </div>
      </div>
    </div>
  );
}
