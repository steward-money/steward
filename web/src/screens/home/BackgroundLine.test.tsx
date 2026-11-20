import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { wrapBook } from '../../test-utils';
import { LiveRegions } from '../../ui/Live';
import { BackgroundLine } from './BackgroundLine';

vi.mock('../../celebrate/celebrate', () => ({ acorn: () => () => {} }));

const AWAY = { pending: 4, done: 0, waiting: true };
const BACK = { pending: 4, done: 1, waiting: false };

/** Holds the background answer in state, so a click flips it inside the same providers. */
function Flip() {
  const [bg, setBg] = useState(AWAY);
  return <><button onClick={() => setBg(BACK)}>flip</button><BackgroundLine bg={bg} className="x" testId="bg" /></>;
}

describe('the background line (phase 17 spec 3.7)', () => {
  it('waiting for the gateway shows the oops face beside the plain line', () => {
    wrapBook(<BackgroundLine bg={AWAY} className="x" testId="bg" />);
    const line = screen.getByTestId('bg');
    expect(line).toHaveTextContent('Waiting for the gateway to categorize 4');
    expect(line.querySelector('svg.acorn')).toHaveAttribute('data-face', 'oops');
  });

  it('with no categorizer coming (no gateway, or no local model), nothing shows: the rows wait in Review (INS-21)', () => {
    wrapBook(<BackgroundLine bg={{ ...AWAY, expected: false }} className="x" testId="bg" />);
    expect(screen.queryByTestId('bg')).toBeNull();
    expect(screen.queryByText(/Waiting for the gateway/)).toBeNull();
  });

  it('when it comes back: the win face and the gatewayBack line', async () => {
    wrapBook(<Flip />);
    fireEvent.click(screen.getByText('flip'));
    expect(await screen.findByText('The gateway is back. Catching up.')).toBeInTheDocument();
    expect(screen.getByTestId('gateway-back').querySelector('svg.acorn')).toHaveAttribute('data-face', 'win');
  });

  it('announces the swap: the line says its words through the polite region, and the back words land inside it', async () => {
    // Issue #51: a live region that appears with its words is often not read, so the line is not one.
    wrapBook(<LiveRegions><Flip /></LiveRegions>);
    const line = screen.getByTestId('bg');
    expect(line).not.toHaveAttribute('role');
    expect(screen.getByTestId('live-said')).toHaveTextContent('Waiting for the gateway to categorize 4');
    fireEvent.click(screen.getByText('flip'));
    expect(await within(line).findByText('The gateway is back. Catching up.')).toBeInTheDocument();
    expect(screen.getByTestId('bg')).toBe(line);
    await waitFor(() => expect(screen.getByTestId('live-said')).toHaveTextContent('The gateway is back. Catching up.'));
  });

  it('reduced motion: the words and the still win face stay', async () => {
    vi.stubGlobal('matchMedia', (q: string) => ({ matches: q.includes('reduce'), media: q, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} }));
    try {
      wrapBook(<Flip />);
      fireEvent.click(screen.getByText('flip'));
      expect(await screen.findByText('The gateway is back. Catching up.')).toBeInTheDocument();
      expect(screen.getByTestId('gateway-back').querySelector('svg.acorn')).toHaveAttribute('data-face', 'win');
      expect(screen.getByTestId('bg')).not.toHaveAttribute('role');
    } finally { vi.unstubAllGlobals(); }
  });

  it('in a parent book: the plain line only, no face either way', async () => {
    wrapBook(<Flip />, { kind: 'parent' });
    expect(document.querySelector('svg.acorn')).toBeNull();
    fireEvent.click(screen.getByText('flip'));
    expect(await screen.findByText('The gateway is back. Catching up.')).toBeInTheDocument();
    expect(document.querySelector('svg.acorn')).toBeNull();
  });

  it('nothing pending: no line', () => {
    wrapBook(<BackgroundLine bg={{ pending: 0, done: 9, waiting: true }} className="x" testId="bg" />);
    expect(screen.queryByTestId('bg')).toBeNull();
  });
});
