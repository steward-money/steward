import { useRef } from 'react';
import { gw } from '../../lib/gatewayName';
import { Button } from '../../ui/Button';
import { CopyButton } from '../../ui/CopyButton';
import { Shown } from '../../ui/Live';
import type { BootLine } from './api';
import { ADD_GATEWAY_LINE, ADD_LINE, CHECK_AGAIN, COPY, COPY_SELECTED, NOT_YET, NOT_YET_UNSET, RESTART, TOOK_EFFECT } from './copy';

/** A line of text to copy, with its Copy button and a note that says what happened (WEB-21). Copied shows only once the
 *  browser says it copied. Plain http has no clipboard, so the line is selected to copy by hand, and the note says so. */
function CopyCode({ text }: { text: string }) {
  const codeRef = useRef<HTMLElement>(null);
  const select = () => {
    const el = codeRef.current;
    const sel = window.getSelection?.();
    if (!el || !sel) return;
    const range = document.createRange();
    range.selectNodeContents(el);
    sel.removeAllRanges();
    sel.addRange(range);
  };
  return (
    <div className="ready__code">
      <code ref={codeRef}>{text}</code>
      <CopyButton text={text} label={COPY} failed={COPY_SELECTED} onFail={select} />
    </div>
  );
}

/** A setting only a file can change: the line to add, where it goes, and Check again, which says whether it took. */
export function SettingLine({ line, done, checked, onCheck, checking }: {
  line: BootLine; done: boolean; checked: boolean; onCheck: () => void; checking: boolean;
}) {
  const gateway = line.where === 'gateway';
  const result = !checked || checking ? null : done ? TOOK_EFFECT
    : line.running === null ? NOT_YET_UNSET : NOT_YET.replace('{value}', line.running);
  return (
    <div className="ready__line stack stack--tight">
      <p>{gateway ? gw(ADD_GATEWAY_LINE) : ADD_LINE}</p>
      <CopyCode text={line.line} />
      {line.note && <p className="small muted">{line.note}</p>}
      {!gateway && <CopyCode text={RESTART} />}
      <div className="ready__check">
        <Button variant="quiet" busy={checking} onClick={onCheck}>{CHECK_AGAIN}</Button>
        {result && <Shown>{result}</Shown>}
      </div>
    </div>
  );
}
