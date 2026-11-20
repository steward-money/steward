import { useQueryClient } from '@tanstack/react-query';
import { useState, type ReactNode } from 'react';
import { useLocation } from 'react-router-dom';
import { useMe, useMembers } from '../../api/hooks';
import type { BookSummary, InvitedRow, Member } from '../../api/types';
import { useBook } from '../../book/BookContext';
import { useWide } from '../../lib/useWide';
import { Acorn } from '../../ui/Acorn';
import { Button } from '../../ui/Button';
import { Card } from '../../ui/Card';
import { ChecklistRow } from '../../ui/ChecklistRow';
import { Shown } from '../../ui/Live';
import { Notice } from '../../ui/Notice';
import { Sheet } from '../../ui/Sheet';
import { Skeleton } from '../../ui/Skeleton';
import { useInstallSettings, useReady, type RowKey } from '../ready/api';
import { BackupsPanel } from '../ready/BackupsPanel';
import { EmailPanel } from '../ready/EmailPanel';
import { GatewayPanel } from '../ready/GatewayPanel';
import { STATUS_WORD } from '../ready/copy';
import '../ready/ready.css';
import { AddBookSheet } from '../settings/AddBookSheet';
import { InviteSheet } from '../settings/InviteSheet';
import { markWelcome } from './api';
import * as C from './copy';
import { useStepMoment } from './FirstRun';

type Extra = 'partner' | Extract<RowKey, 'ask' | 'email' | 'backups'>;

/** The ready screen shows times as the server words them ("3:00 AM"); a time off the clock reads the same here. */
const clock = (hhmm: string): string | null => {
  const m = /^(\d{2}):(\d{2})$/.exec(hhmm);
  if (!m) return null;
  const h = Number(m[1]);
  return `${h % 12 || 12}:${m[2]} ${h < 12 ? 'AM' : 'PM'}`;
};

type RowProps = { id: Extra; title: string; line: string; go: string; done: boolean; status: string; onClick: () => void;
  expanded: boolean; owed?: boolean; children?: ReactNode };

/** One extra: its row, its panel in place, and the hop and line when it turns done while the page is open. */
function ExtraRow({ id, title, line, go, done, status, onClick, expanded, children }: RowProps) {
  const { ref, line: said, played } = useStepMoment(done, id);
  return (
    <div className="welcome__row">
      <ChecklistRow testId="extras-row" done={done} title={title} detail={line} go={go} status={status} onClick={onClick} expanded={expanded} />
      <Shown as="div" className="welcome__said">
        {played && <Acorn ref={ref} face="win" size={24} />}
        {said && <p data-testid="step-line">{said}</p>}
      </Shown>
      {children}
    </div>
  );
}

/** The first run's last screen (spec 2.5): four optional extras with their live status, the help card, and Finish. */
export function Extras({ again, go }: { again: boolean; go: (to: 'planted') => void }) {
  const book = useBook();
  const slug = book?.slug ?? 'ours';
  const qc = useQueryClient();
  const ready = useReady();
  const settings = useInstallSettings();
  const members = useMembers(slug);
  const me = useMe();
  const wide = useWide();
  const loc = useLocation();
  const fromSort = (loc.state as { setupDone?: string } | null)?.setupDone === 'sort';
  const sorted = useStepMoment(fromSort, 'sort', true);
  const [open, setOpen] = useState<Extra | null>(null);
  const [adding, setAdding] = useState(false);
  const [added, setAdded] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [inviteLine, setInviteLine] = useState('');

  const finish = async () => {
    setBusy(true);
    if (!again && book) { try { await markWelcome(book.slug); } catch { /* the welcome comes back next time */ } }
    go('planted');
  };

  const rows = ready.data?.rows;
  // Wait for both answers before any row shows: a row that arrives done is not a row that turned done.
  const loaded = rows !== undefined && !members.isPending;
  const rowOf = (key: RowKey) => rows?.find((r) => r.key === key);
  const others = (members.data?.members ?? []).some((m: Member | InvitedRow) => m.user_id === null || m.user_id !== me.data?.user.id);

  const panel = (id: Exclude<Extra, 'partner'>): ReactNode => {
    const row = rowOf(id);
    if (!row) return null;
    if (id === 'ask') return <GatewayPanel row={row} onCheck={() => void ready.refetch()} checking={ready.isFetching} />;
    const s = settings.data;
    if (settings.isError) return <Notice tone="error">{settings.error.message}</Notice>;
    if (!s?.email) return <Skeleton lines={3} />;
    return id === 'email' ? <EmailPanel s={s.email} /> : <BackupsPanel s={s.backups} row={row} />;
  };
  const extra = (id: Exclude<Extra, 'partner'>, title: string, line: string, go: string) => {
    const row = rowOf(id);
    if (!row) return null;
    const isOpen = open === id;
    const body = isOpen ? panel(id) : null;
    return (
      <ExtraRow key={id} id={id} title={title} line={line} go={go} done={row.status === 'done'} status={STATUS_WORD[row.status]}
        expanded={isOpen} onClick={() => setOpen(isOpen ? null : id)}>
        {isOpen && wide && body && <div className="ready__panel welcome__panel">{body}</div>}
        {isOpen && !wide && body && <Sheet open onClose={() => setOpen(null)} title={title}>{body}</Sheet>}
      </ExtraRow>
    );
  };

  const at = settings.data?.backups.at.value ?? '';
  const time = clock(at);
  const backups = time ? C.backupsLine(time) : rowOf('backups')?.detail ?? '';

  return (
    <section className="welcome__step" aria-labelledby="welcome-extras-h">
      <div className="welcome__headline">
        {sorted.played && <Acorn ref={sorted.ref} face="win" size={48} className="welcome__acorn" />}
        <h1 id="welcome-extras-h" className="welcome__title">{C.EXTRAS_TITLE}</h1>
      </div>
      <Shown as="div" className="welcome__said">{sorted.line && <p data-testid="step-line">{sorted.line}</p>}</Shown>
      <p className="welcome__lede">{C.EXTRAS_LEDE}</p>
      {!loaded ? (ready.isError ? <Notice tone="error">{ready.error.message}</Notice> : <Skeleton lines={4} />) : (
        <div className="card setup welcome__rows">
          <ExtraRow id="partner" title={C.PARTNER} line={C.PARTNER_LINE} go={C.INVITE} done={others}
            status={others ? STATUS_WORD.done : STATUS_WORD.optional} expanded={false} onClick={() => { setInviteLine(''); setOpen('partner'); }} />
          {inviteLine && <Shown className="small muted welcome__invite-line">{inviteLine}</Shown>}
          {extra('ask', C.ASK, C.ASK_LINE, C.SET_UP_ROW)}
          {extra('email', C.EMAIL, C.EMAIL_LINE, C.SET_UP_ROW)}
          {extra('backups', C.BACKUPS, backups, C.CHECK)}
        </div>
      )}
      <Card className="welcome__card" data-testid="help-card">
        <h2 className="subhead">{C.HELP_CARD_TITLE}</h2>
        <p className="muted">{C.HELP_CARD_LINE}</p>
        {added.map((name) => <Shown key={name}>{C.inYourBooks(name)}</Shown>)}
        <Button variant="quiet" onClick={() => setAdding(true)}>{C.ADD_THEIR_BOOK}</Button>
      </Card>
      <div className="welcome__foot">
        <Button busy={busy} onClick={() => void finish()}>{C.FINISH}</Button>
      </div>
      {open === 'partner' && (
        <InviteSheet slug={slug} onClose={() => setOpen(null)}
          onDone={(line) => { setInviteLine(line); setOpen(null); void qc.invalidateQueries({ queryKey: ['members'] }); }} />
      )}
      {adding && <AddBookSheet onClose={() => setAdding(false)} onAdded={(b: BookSummary) => { setAdding(false); setAdded((a) => [...a, b.name]); }} />}
    </section>
  );
}
