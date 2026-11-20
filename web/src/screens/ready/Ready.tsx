import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Link } from '../../book/links';
import { soundOn } from '../../lib/celebrations';
import { S_BANKS_AND_ACCOUNTS, S_READY, SIGN_IN_STEP, SIGN_IN_STEP_UNLINKED, TRY_AGAIN } from '../../lib/copy';
import { nextLine } from '../../lib/lines';
import { momentAllowed } from '../../lib/momentAllowed';
import { Acorn } from '../../ui/Acorn';
import { useAcornMotion } from '../../ui/useMoment';
import { isSection, sectionPath } from '../../lib/profileSections';
import { useWide } from '../../lib/useWide';
import { Button } from '../../ui/Button';
import { ChecklistRow } from '../../ui/ChecklistRow';
import { Shown } from '../../ui/Live';
import { Notice } from '../../ui/Notice';
import { Sheet } from '../../ui/Sheet';
import { Skeleton } from '../../ui/Skeleton';
import { AddBookSheet } from '../settings/AddBookSheet';
import { useInstallSettings, useReady, type BootLine, type ReadyRow, type RowKey } from './api';
import {
  IMPORT_FILE, LAN_GUIDE, LAN_GUIDE_LINK, LINK_BANK, NAME_IT, NEED, NOTHING_NEEDS, PASSKEY_AT_IP, PASSKEY_UNTRUSTED, STATUS_WORD, TITLES,
  TRUST_ROOT,
} from './copy';
import { BackupsPanel } from './BackupsPanel';
import { EmailPanel } from './EmailPanel';
import { GatewayPanel } from './GatewayPanel';
import { usePasskeyBlock } from './passkeyProbe';
import { SettingLine } from './SettingLine';
import { StewardSettings } from './StewardSettings';
import './ready.css';

export default function Ready() {
  const ready = useReady();
  const settings = useInstallSettings();
  const wide = useWide();
  const [open, setOpen] = useState<RowKey | null>(null);
  const [adding, setAdding] = useState(false);
  // The last need seen: when it drops from above 0 to 0 while the page is open, the lede cheers once (D16).
  const lastNeed = useRef<number | null>(null);
  const [cheer, setCheer] = useState(false);
  const acornRef = useRef<SVGSVGElement>(null);
  const allowed = momentAllowed('setupStep', { parent: false });
  // The server can only match the address; whether this browser can use passkeys there, only the browser can tell (INS-22).
  const block = usePasskeyBlock(ready.data?.rows.some((r) => r.key === 'address' && r.status === 'done') ?? false);
  const rows = ready.data?.rows.map((r): ReadyRow => (r.key === 'address' && block
    ? { ...r, status: 'needs', detail: block === 'certificate' ? PASSKEY_UNTRUSTED : PASSKEY_AT_IP } : r));
  const need = ready.data && ready.data.need + (block ? 1 : 0);
  useEffect(() => {
    if (need === undefined) return;
    if (lastNeed.current !== null && lastNeed.current > 0 && need === 0) setCheer(true);
    lastNeed.current = need;
  }, [need]);
  useAcornMotion(acornRef, cheer && allowed.motion ? 'hop' : null, allowed.sound && soundOn());
  // The lines a row showed when Check again was pressed: once the restart took, the row has none left to show,
  // and the page still says it took effect.
  const [checked, setChecked] = useState<Partial<Record<RowKey, BootLine[]>>>({});

  if (ready.isError) {
    return <div className="stack"><h2>{S_READY}</h2><Notice tone="error"><p>{ready.error.message}</p><Button variant="quiet" onClick={() => ready.refetch()}>{TRY_AGAIN}</Button></Notice></div>;
  }
  if (!ready.data || !rows || need === undefined) return <div className="stack" aria-busy="true"><h2>{S_READY}</h2><Skeleton lines={8} /></div>;

  const check = (row: ReadyRow) => {
    setChecked((c) => ({ ...c, [row.key]: row.lines.length ? row.lines : c[row.key] ?? [] }));
    void ready.refetch();
  };
  const panel = (row: ReadyRow): ReactNode => {
    if (row.key === 'money' && Array.isArray(row.extra.sign_in) && row.extra.sign_in.length > 0) {
      // Unlinking leaves the warnings open, so the step follows Banks: Update now only while SimpleFIN is linked.
      return <><p>{row.extra.linked === true ? SIGN_IN_STEP : SIGN_IN_STEP_UNLINKED}</p><p className="ready__links"><Link className="link" to="/settings/banks">{S_BANKS_AND_ACCOUNTS}</Link></p></>;
    }
    if (row.key === 'money') {
      return <p className="ready__links"><Link className="link" to="/settings/banks">{LINK_BANK}</Link><Link className="link" to="/settings/import">{IMPORT_FILE}</Link></p>;
    }
    if (row.key === 'ask') {
      const lines = row.lines.length ? row.lines : checked.ask ?? [];
      return <GatewayPanel row={{ ...row, lines }} onCheck={() => check(row)} checking={ready.isFetching} />;
    }
    if (row.key === 'email' || row.key === 'backups') {
      const s = settings.data;
      if (settings.isError) return <Notice tone="error">{settings.error.message}</Notice>;
      if (!s?.email) return <Skeleton lines={3} />;
      return row.key === 'email' ? <EmailPanel s={s.email} /> : <BackupsPanel s={s.backups} row={row} />;
    }
    if (row.key === 'address' && block) {
      return <p>{block === 'certificate' ? TRUST_ROOT : NAME_IT} <a className="link" href={LAN_GUIDE} target="_blank" rel="noreferrer">{LAN_GUIDE_LINK}</a></p>;
    }
    if (row.key === 'address' || row.key === 'key') {
      const lines = row.lines.length ? row.lines : checked[row.key] ?? [];
      if (!lines.length) return null;
      return lines.map((l) => (
        <SettingLine key={l.name} line={l} done={row.status === 'done'} checked={row.key in checked} checking={ready.isFetching} onCheck={() => check(row)} />
      ));
    }
    return null;
  };

  return (
    <div className="stack ready">
      <h2>{S_READY}</h2>
      {cheer && need === 0 ? (
        <Shown className="lede ready__cheer">
          {allowed.face && <Acorn ref={acornRef} face="win" size={28} />}
          {allowed.line ? nextLine('ready') : NOTHING_NEEDS}
        </Shown>
      ) : <p className="lede">{need === 0 ? NOTHING_NEEDS : NEED(need)}</p>}
      <div className="card setup">
        {rows.map((row) => {
          const common = { done: row.status === 'done', needs: row.status === 'needs', status: STATUS_WORD[row.status],
            title: TITLES[row.key], detail: row.detail, go: row.go ?? undefined, testId: 'ready-row' };
          if (row.key === 'household') {
            const next = row.extra.next;
            return <div className="ready__item" key={row.key}><ChecklistRow {...common} to={typeof next === 'string' && isSection(next) ? sectionPath(next, null) : '/plan'} /></div>;
          }
          if (row.key === 'someone') {
            return (
              <div className="ready__item" key={row.key}>
                <ChecklistRow {...common} onClick={() => setAdding(true)} />
              </div>
            );
          }
          const isOpen = open === row.key;
          const body = isOpen ? panel(row) : null;
          return (
            <div className="ready__item" key={row.key}>
              <ChecklistRow {...common} onClick={() => setOpen(isOpen ? null : row.key)} expanded={isOpen} />
              {isOpen && wide && body && <div className="ready__panel">{body}</div>}
              {isOpen && !wide && body && <Sheet open onClose={() => setOpen(null)} title={TITLES[row.key]}>{body}</Sheet>}
            </div>
          );
        })}
      </div>
      {settings.data?.general ? <StewardSettings s={settings.data.general} /> : settings.isPending && <div className="card"><Skeleton lines={3} /></div>}
      {adding && <AddBookSheet onClose={() => setAdding(false)} />}
    </div>
  );
}
