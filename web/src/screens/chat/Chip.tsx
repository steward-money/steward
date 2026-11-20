// Replaced parts (design README): plum with a shield for Steward's exact replacements, amber with a scanning eye for
// The gateway's model check. The icon carries the difference as well as the colour. Tapping one says which layer and why.
import { ScanEye, ShieldCheck } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import { fill, isModelChip, type Chip } from './askApi';
import * as C from './copy';
import { gw } from '../../lib/gatewayName';
import { Shown } from '../../ui/Live';

export function chipWhy(c: Chip, labels: Record<string, string>): string[] {
  if (isModelChip(c)) return [fill(gw(C.POP_MODEL), { what: C.WHAT_MODEL[c.label] ?? "something that could identify someone, and that isn't sent" }), C.POP_MODEL_AFTER];
  const label = labels[c.label];
  if (label && c.label.startsWith('Account ')) return [fill(C.POP_ACCOUNT, { label, alias: c.label })];
  if (label && c.label.startsWith('Payee ')) return [fill(C.POP_PAYEE, { label, alias: c.label })];
  if (label) return [fill(C.POP_PERSON, { label, alias: c.label })];
  if (c.label === '[secret]') return [C.POP_STEWARD_SECRET];
  return [fill(C.POP_STEWARD_BRACKET, { what: C.WHAT_STEWARD[c.label] ?? 'something' })];
}

export function Legend() {
  return (
    <p className="tok-legend">
      <span><ShieldCheck size={20} aria-hidden="true" />{C.LEGEND_STEWARD}</span>
      <span><ScanEye size={20} aria-hidden="true" />{gw(C.LEGEND_MODEL)}</span>
    </p>
  );
}

export function ChipText({ text, chips, labels }: { text: string; chips: Chip[]; labels: Record<string, string> }) {
  const [open, setOpen] = useState<number | null>(null);
  const out: ReactNode[] = [];
  let at = 0;
  chips.forEach((c, i) => {
    out.push(text.slice(at, c.start));
    const model = isModelChip(c);
    out.push(
      <button key={i} type="button" className={`tok${model ? ' tok--model' : ''}${open === i ? ' is-open' : ''}`}
        aria-expanded={open === i} onClick={() => setOpen(open === i ? null : i)}>
        {model ? <ScanEye size={20} aria-hidden="true" /> : <ShieldCheck size={20} aria-hidden="true" />}{text.slice(c.start, c.end)}
        <span className="tok__said">{model ? gw(C.CHIP_SAID_MODEL) : C.CHIP_SAID_STEWARD}</span>
      </button>,
    );
    at = c.end;
  });
  out.push(text.slice(at));
  const chip = open === null ? null : chips[open];
  return (
    <>
      <span className="willsend__text">{out}</span>
      {chip && (
        <Shown as="span" className="tok-pop">
          <span className="strong tok-pop__head">{isModelChip(chip) ? <ScanEye size={20} aria-hidden="true" /> : <ShieldCheck size={20} aria-hidden="true" />}{chip.label}</span>
          {chipWhy(chip, labels).map((line, i) => <span key={i} className={i ? 'small muted' : undefined}>{line}</span>)}
        </Shown>
      )}
    </>
  );
}
