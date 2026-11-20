import { useQueryClient } from '@tanstack/react-query';
import { Check, Circle } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { currentBook } from '../../api/book';
import { seeGrowth, useGrowth, type Growth } from '../../api/growth';
import { useIsHousehold } from '../../book/BookContext';
import { soundOn } from '../../lib/celebrations';
import { gw } from '../../lib/gatewayName';
import { fill, GROWTH_ALL, GROWTH_NEXT, MILESTONE_NAME, MILESTONE_NOT_YET, MILESTONE_REACHED, MONTH_SORTED, NEW_GROWTH, NEW_GROWTH_PLAIN, nextLine, TREE_LABEL } from '../../lib/lines';
import { momentAllowed } from '../../lib/momentAllowed';
import { setQueryDataIfStillIn } from '../../queryClient';
import { Acorn } from '../../ui/Acorn';
import { Shown } from '../../ui/Live';
import { monthLong, monthName } from '../cashflow/months';
import { OakTree } from '../../ui/OakTree';
import { useAcornMotion, useWinMoment } from '../../ui/useMoment';
import './moments.css';

// Phase 17 spec 3.4 and 3.7: Home's moments, in their own small chunk (lazyMoments.ts), because their lines come from
// lib/lines.ts, which never joins the initial bundle.

export const treeLabel = (stage: number): string => fill(TREE_LABEL, { n: stage });

/** "3 of 5: a year of history comes next", or a full-grown oak. */
export function progressLine(g: Growth): string {
  const next = g.milestones.find((m) => !m.met);
  return g.stage >= 5 || !next ? GROWTH_ALL : fill(GROWTH_NEXT, { n: g.stage, next: MILESTONE_NAME[next.key] });
}

/** The line for a new stage: the last milestone in order that is met (the stage counts them; which one came last isn't
 *  kept), or a plain one when the tree holds a stage its data no longer shows. The first month says which month it
 *  was, with its year when that isn't the year of the server's day, as_of (#33), never the browser's clock (P41). */
export function newGrowthLine(g: Growth, asOf: string): string {
  const met = [...g.milestones].reverse().find((m) => m.met);
  if (!met) return NEW_GROWTH_PLAIN;
  const month = met.key === 'first_month' && met.month ? met.month : null;
  const name = month === null ? MILESTONE_NAME[met.key]
    : fill(MONTH_SORTED, { month: month.slice(0, 4) === asOf.slice(0, 4) ? monthName(month) : monthLong(month) });
  return fill(NEW_GROWTH, { milestone: name });
}

/** Spec 3.7: the usual pay landed since the last look. One coin moment on that visit; the line for the rest of the day. */
export function PaydayMoment({ fresh }: { fresh: boolean }) {
  const allowed = momentAllowed('payday', { parent: false });
  const [line] = useState(() => nextLine('payday'));
  const host = useRef<HTMLParagraphElement>(null);
  useWinMoment(host, fresh && allowed.motion ? 'coins' : null, allowed.sound && soundOn());
  return <p ref={host} className="verdict__payday verdict__payday--came" data-testid="payday-came">{line}</p>;
}

/** Spec 3.4: the tree in its corner of Home, below everything else. A stage this person hasn't seen hops once, with a
 *  line naming the milestone, then is marked seen. `asOf` is Home's day from the server. */
export function HomeTree({ asOf }: { asOf: string }) {
  const allowed = momentAllowed('tree', { parent: !useIsHousehold() });
  const q = useGrowth(allowed.face);
  const qc = useQueryClient();
  const g = q.data;
  const svg = useRef<SVGSVGElement>(null);
  const fresh = g !== undefined && g.stage > 0 && !g.seen;
  const [said, setSaid] = useState<string | null>(null);
  useEffect(() => {
    if (!fresh || !g) return;
    setSaid(newGrowthLine(g, asOf));
    const book = currentBook();
    void seeGrowth(g.stage).then(
      () => { setQueryDataIfStillIn<Growth>(qc, book, ['growth'], (old) => (old ? { ...old, seen: true } : old)); },
      () => undefined,                       // not marked: the hop plays again next time, which is harmless
    );
  }, [fresh, g?.stage]); // eslint-disable-line react-hooks/exhaustive-deps -- once per stage shown
  useAcornMotion(svg, said && allowed.motion ? 'hop' : null, allowed.sound && soundOn());
  if (!allowed.face || !g) return null;
  return (
    <aside className="home-tree" data-testid="home-tree">
      {said && <Shown className="home-tree__line">{said}</Shown>}
      <OakTree ref={svg} stage={g.stage} label={treeLabel(g.stage)} />
    </aside>
  );
}

/** Spec 3.4: the tree on Profile, with the milestone list. */
export function ProfileGrowth() {
  const allowed = momentAllowed('tree', { parent: !useIsHousehold() });
  const q = useGrowth(allowed.face);
  if (!allowed.face || !q.data) return null;
  const g = q.data;
  return (
    <section className="card prof-growth" data-testid="profile-growth" aria-label={treeLabel(g.stage)}>
      <OakTree stage={g.stage} label={treeLabel(g.stage)} size={72} />
      <div className="prof-growth__text">
        <p className="prof-growth__line">{progressLine(g)}</p>
        <ul className="plain prof-growth__list">
          {g.milestones.map((m) => (
            <li key={m.key} className={m.met ? 'is-met' : undefined}>
              {m.met ? <Check className="i" size={16} aria-hidden="true" /> : <Circle className="i" size={16} aria-hidden="true" />}
              <span>{MILESTONE_NAME[m.key][0].toUpperCase() + MILESTONE_NAME[m.key].slice(1)}</span>
              <span className="prof-growth__state">{m.met ? MILESTONE_REACHED : MILESTONE_NOT_YET}</span>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}

/** Spec 3.7: the gateway is back and catching up. One hop and the line; a parent book gets the line only. */
export function GatewayBack() {
  const allowed = momentAllowed('gatewayBack', { parent: !useIsHousehold() });
  const svg = useRef<SVGSVGElement>(null);
  const [line] = useState(() => gw(nextLine('gatewayBack')));
  useAcornMotion(svg, allowed.motion ? 'hop' : null, allowed.sound && soundOn());
  return <span className="gateway-back" data-testid="gateway-back">{allowed.face && <Acorn ref={svg} face="win" size={20} />}{line}</span>;
}
