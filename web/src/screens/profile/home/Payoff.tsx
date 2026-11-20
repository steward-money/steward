import { Calculator, Pencil } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { monthLabel } from '../../../lib/format';
import { Field, FormError } from '../../../ui/Field';
import { Shown, useArriving } from '../../../ui/Live';
import { failure } from '../../settings/shared';
import { liveCalc, ym } from '../home';
import { payoffYmd, thisMonth } from '../payoff';
import { saveFact } from '../api';
import {
  CALC_HOW, CALC_HOW_ONE, CALC_WAITING, ENTER_DATE, NEVER_PAID, OVERRIDE_HOW, OVERRIDE_ONLY, PAID_OFF_BY, PAID_OFF_NOW, PAYOFF_SET, USE_CALC,
} from '../copy';
import { useFieldMode } from '../field';
import type { Offer } from './offer';

/** The payoff date follows the calculation, but only once the person has touched the rate, the payment or the date. */
export function Payoff({ owedCents, rate, payment, saved, offer }: { owedCents: number | null; rate: string; payment: string; saved: string | null; offer: Offer }) {
  const { register, canEdit } = useFieldMode();
  // Review of #51: the readout shows the saved loan, so it is quiet when the step opens; a change is said, and so is
  // the readout coming back after Use the calculated date.
  const arriving = useArriving();
  const from = useMemo(() => thisMonth(), []);
  const calc = liveCalc(owedCents, rate, payment, from);
  const calcYm = calc?.kind === 'months' ? ym(calc) : calc?.kind === 'paid_off' ? ym(from) : null;
  const [overriding, setOverriding] = useState(saved !== null && saved.slice(0, 7) !== calcYm);
  const [typed, setTyped] = useState(saved?.slice(0, 7) ?? '');
  const [error, setError] = useState<string | null>(null);
  const want = overriding ? (/^\d{4}-(0[1-9]|1[0-2])$/.test(typed) ? typed : null) : calcYm;
  const sent = useRef(saved?.slice(0, 7) ?? null);
  const tell = useRef(offer);
  tell.current = offer;
  const first = useRef({ rate, payment });
  const [touched, setTouched] = useState(false);
  const dirty = touched || rate !== first.current.rate || payment !== first.current.payment;
  const commit = useCallback(async (): Promise<boolean> => {
    if (!dirty || want === null || want === sent.current) return true;
    try {
      const token = await saveFact('mortgage.payoff_date', `${want}-01`);
      sent.current = want;
      tell.current(PAYOFF_SET, token);
      setError(null);
      return true;
    } catch (e) {
      setError(failure(e));
      return false;
    }
  }, [want, dirty]);
  useEffect(() => register(commit), [register, commit]);
  useEffect(() => { if (!dirty) return; const t = window.setTimeout(() => void commit(), 600); return () => window.clearTimeout(t); }, [commit, dirty]);
  const how = calc?.kind === 'months' ? (calc.payments === 1 ? CALC_HOW_ONE : CALC_HOW.replace('{n}', String(calc.payments))) : null;
  if (overriding && !canEdit) {      // a viewer reads the date that was entered, with no way to change it
    return (
      <Shown as="div" className="calc" data-testid="payoff" quietOnMount={arriving}>
        <span className="calc__label"><Calculator className="i" size={20} aria-hidden="true" />{PAID_OFF_BY}</span>
        <span className="calc__value">{saved ? monthLabel(saved) : ''}</span>
      </Shown>
    );
  }
  if (overriding) {
    return (
      <div className="calc calc--override">
        <Field label={PAID_OFF_BY} error={error ?? undefined}><input type="month" value={typed} onChange={(e) => { setTyped(e.target.value); setTouched(true); }} /></Field>
        <span className="calc__how">{calcYm ? OVERRIDE_HOW.replace('{date}', monthLabel(`${calcYm}-01`)) : OVERRIDE_ONLY}</span>
        <button type="button" className="link" onClick={() => { setOverriding(false); setTouched(true); }}>{USE_CALC}</button>
      </div>
    );
  }
  return (
    <Shown as="div" className="calc" data-testid="payoff" quietOnMount={arriving}>
      <span className="calc__label"><Calculator className="i" size={20} aria-hidden="true" />{PAID_OFF_BY}</span>
      {calc === null ? <span className="calc__how">{CALC_WAITING}</span>
        : calc.kind === 'never' ? <span className="calc__how warn">{NEVER_PAID}</span>
        : calc.kind === 'paid_off' ? <span className="calc__value">{PAID_OFF_NOW}</span>
        : <><span className="calc__value">{monthLabel(payoffYmd(calc))}</span><span className="calc__how">{how}</span></>}
      {canEdit && (
        <button type="button" className="link" onClick={() => { setTyped(calcYm ?? ''); setOverriding(true); setTouched(true); }}>
          <Pencil className="i" size={20} aria-hidden="true" />{ENTER_DATE}
        </button>
      )}
      {error && <FormError>{error}</FormError>}
    </Shown>
  );
}
