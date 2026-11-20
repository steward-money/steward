// Claude writes aliases ("Person 2"); Steward shows the real label with a dotted underline, and a tap or hover says
// what was sent (spec 4.2, JR decision 5). Answers are plain text: paragraphs, **bold**, "- " list lines, and dollar
// amounts set strong so the numbers lead (design README, "The voice").
import { Fragment, useState, type ReactNode } from 'react';
import { Shown } from '../../ui/Live';
import { ALIAS_TIP } from './copy';
import { fill } from './askApi';

const ALIAS = /(?<![\w])(?:(?:Person|Child|Account|Payee) [1-9][0-9]{0,3}|Joint|Business|Parent)(?![\w])/g;
const MONEY = /(?<![\w$])-?\$[0-9][0-9,]*(?:\.[0-9]{2})?(?:[kKmM]\b)?/g;

function Alias({ label, alias }: { label: string; alias: string }) {
  const [open, setOpen] = useState(false);
  const tip = fill(ALIAS_TIP, { alias });
  return (
    <button type="button" className="alias" title={tip} aria-label={`${label}. ${tip}`} aria-expanded={open}
      onClick={() => setOpen((o) => !o)}>
      {label}{open && <Shown as="span" className="alias__tip">{tip}</Shown>}
    </button>
  );
}

function withMoney(text: string, key: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  for (const m of text.matchAll(MONEY)) {
    out.push(text.slice(last, m.index));
    out.push(<span key={`${key}-${m.index}`} className="num">{m[0]}</span>);
    last = (m.index ?? 0) + m[0].length;
  }
  out.push(text.slice(last));
  return out;
}

export function AliasText({ text, labels, money = false }: { text: string; labels: Record<string, string>; money?: boolean }) {
  const out: ReactNode[] = [];
  let last = 0;
  for (const m of text.matchAll(ALIAS)) {
    const label = labels[m[0]];
    if (!label || label === m[0]) continue;
    const before = text.slice(last, m.index);
    out.push(money ? <Fragment key={`t${last}`}>{withMoney(before, `m${last}`)}</Fragment> : before);
    out.push(<Alias key={`a${m.index}`} label={label} alias={m[0]} />);
    last = (m.index ?? 0) + m[0].length;
  }
  const tail = text.slice(last);
  out.push(money ? <Fragment key="tail">{withMoney(tail, 'mt')}</Fragment> : tail);
  return <>{out}</>;
}

function Inline({ text, labels }: { text: string; labels: Record<string, string> }) {
  const parts = text.split(/\*\*(.+?)\*\*/g);
  return <>{parts.map((p, i) => (i % 2 === 1
    ? <strong key={i}><AliasText text={p} labels={labels} /></strong>
    : <AliasText key={i} text={p} labels={labels} money />))}</>;
}

type Align = 'left' | 'right' | 'center' | undefined;
type Segment = { kind: 'table' | 'text'; lines: string[] };

// A row's cells, trimmed; "\|" stays a literal pipe inside a cell.
function cells(line: string): string[] {
  const inner = line.trim().replace(/^\|/, '').replace(/(?<!\\)\|$/, '');
  return inner.split(/(?<!\\)\|/).map((c) => c.replace(/\\\|/g, '|').trim());
}

// The separator row ("|---|:---:|"): one alignment marker per column, or null when the line is not one.
function separator(line: string): Align[] | null {
  const marks = cells(line);
  if (!marks.every((m) => /^:?-+:?$/.test(m))) return null;
  return marks.map((m) => (m.startsWith(':') ? (m.endsWith(':') ? 'center' : 'left') : m.endsWith(':') ? 'right' : undefined));
}

const NUMERIC = /^-?\$?[0-9][0-9,]*(?:\.[0-9]+)?(?:[kKmM]|%)?$/;

// Marked columns keep their marker; an unmarked column of only numbers or money reads best against the right edge.
function alignments(marked: Align[], body: string[][]): Align[] {
  return marked.map((a, c) => a ?? (body.every((r) => NUMERIC.test(r[c].replace(/\*\*/g, ''))) ? 'right' : undefined));
}

// Header, separator, then rows of the header's width; anything else is not a table (the caller shows the raw lines).
function parseTable(lines: string[]): { head: string[]; body: string[][]; align: Align[] } | null {
  const marked = lines.length > 1 ? separator(lines[1]) : null;
  const head = cells(lines[0]);
  const body = lines.slice(2).map(cells);
  if (!marked || marked.length !== head.length || body.some((r) => r.length !== head.length)) return null;
  return { head, body, align: alignments(marked, body) };
}

// Consecutive "|" lines are a table run; the lines around them stay as they are.
function segments(lines: string[]): Segment[] {
  const out: Segment[] = [];
  for (const l of lines) {
    const kind = l.startsWith('|') ? 'table' : 'text';
    if (out.at(-1)?.kind === kind) out.at(-1)!.lines.push(l);
    else out.push({ kind, lines: [l] });
  }
  return out;
}

function AnswerTable({ lines, labels }: { lines: string[]; labels: Record<string, string> }) {
  const t = parseTable(lines);
  if (!t) return <pre className="answer-table">{lines.join('\n')}</pre>;
  const cell = (text: string) => <Inline text={text} labels={labels} />;
  return (
    <div className="answer-table">
      <table>
        <thead><tr>{t.head.map((h, c) => <th key={c} scope="col" style={{ textAlign: t.align[c] }}>{cell(h)}</th>)}</tr></thead>
        <tbody>{t.body.map((r, i) => <tr key={i}>{r.map((x, c) => <td key={c} style={{ textAlign: t.align[c] }}>{cell(x)}</td>)}</tr>)}</tbody>
      </table>
    </div>
  );
}

function TextLines({ lines, labels }: { lines: string[]; labels: Record<string, string> }) {
  if (lines.every((l) => /^[-•] /.test(l))) {
    return <ul className="answer-list">{lines.map((l, j) => <li key={j}><Inline text={l.slice(2)} labels={labels} /></li>)}</ul>;
  }
  return <p>{lines.map((l, j) => <Fragment key={j}>{j > 0 && <br />}<Inline text={l} labels={labels} /></Fragment>)}</p>;
}

export function AnswerText({ text, labels }: { text: string; labels: Record<string, string> }) {
  const blocks = text.split(/\n{2,}/).map((b) => b.trim()).filter(Boolean);
  return (
    <>
      {blocks.flatMap((b, i) => segments(b.split('\n')).map((seg, j) => (seg.kind === 'table'
        ? <AnswerTable key={`${i}-${j}`} lines={seg.lines} labels={labels} />
        : <TextLines key={`${i}-${j}`} lines={seg.lines} labels={labels} />)))}
    </>
  );
}
