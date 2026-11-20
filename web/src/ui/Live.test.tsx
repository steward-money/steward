import { fireEvent, render, screen } from '@testing-library/react';
import { useRef, useState } from 'react';
import { describe, expect, it } from 'vitest';
import { LiveRegions, Shown, useArriving } from './Live';

// Issue #51: a line that appears with its words already in it (Saved, a result, a cheer) is said through the page's
// polite region, which was there before it; the line itself is not a live region.
describe('Shown', () => {
  it('says its words through the polite region and is not a live region itself', () => {
    render(<LiveRegions><Shown className="small" data-testid="line">Saved</Shown></LiveRegions>);
    expect(screen.getByTestId('live-said')).toHaveTextContent('Saved');
    const line = screen.getByTestId('line');
    expect(line.tagName).toBe('P');
    expect(line).toHaveClass('small');
    expect(line).not.toHaveAttribute('role');
    expect(line).not.toHaveAttribute('aria-live');
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('renders as the element asked for', () => {
    render(<LiveRegions><Shown as="span" data-testid="line">Saved</Shown></LiveRegions>);
    expect(screen.getByTestId('line').tagName).toBe('SPAN');
  });

  it('is said again each time it appears, and when its words change', async () => {
    const moment = () => Promise.resolve();    // lines said in one moment are said together (Live.tsx)
    function Saving() {
      const [on, setOn] = useState(true);
      const [n, setN] = useState(1);
      return (
        <>
          {on && <Shown>{`Saved ${n}`}</Shown>}
          <button type="button" onClick={() => setOn((x) => !x)}>Toggle</button>
          <button type="button" onClick={() => setN((x) => x + 1)}>More</button>
        </>
      );
    }
    render(<LiveRegions><Saving /></LiveRegions>);
    const region = screen.getByTestId('live-said');
    expect(region.textContent?.trim()).toBe('Saved 1');
    await moment();
    region.textContent = '';
    fireEvent.click(screen.getByRole('button', { name: 'Toggle' }));
    fireEvent.click(screen.getByRole('button', { name: 'Toggle' }));
    expect(region.textContent?.trim()).toBe('Saved 1');
    await moment();
    fireEvent.click(screen.getByRole('button', { name: 'More' }));
    expect(region.textContent?.trim()).toBe('Saved 2');
  });

  it('uses a ref it is given, so a moment can play on the same element', () => {
    function Host() {
      const host = useRef<HTMLParagraphElement>(null);
      const [tag, setTag] = useState('');
      return (
        <>
          <Shown ref={host}>Three rows in.</Shown>
          <button type="button" onClick={() => setTag(host.current?.textContent ?? 'none')}>Read</button>
          <output>{tag}</output>
        </>
      );
    }
    render(<LiveRegions><Host /></LiveRegions>);
    fireEvent.click(screen.getByRole('button', { name: 'Read' }));
    expect(screen.getByRole('status')).toHaveTextContent('Three rows in.');   // the <output>
    expect(screen.getByTestId('live-said')).toHaveTextContent('Three rows in.');
  });

  it('does not say an alert inside it, which speaks for itself', () => {
    render(<LiveRegions><Shown as="div">Paid off by March 2031<p role="alert">That didn’t save.</p></Shown></LiveRegions>);
    expect(screen.getByTestId('live-said').textContent).toBe('Paid off by March 2031');
  });

  it('does not say a line inside a part hidden from screen readers', () => {
    render(
      <LiveRegions>
        <div aria-hidden="true"><Shown>Hidden 1</Shown></div>
        <div inert><Shown>Hidden 2</Shown></div>
        <div hidden><Shown>Hidden 3</Shown></div>
      </LiveRegions>,
    );
    expect(screen.getByTestId('live-said').textContent).toBe('');
  });

  it('says the line once the part around it is no longer hidden', () => {
    function Host() {
      const [hide, setHide] = useState(true);
      return (
        <>
          <div aria-hidden={hide || undefined}><Shown>Saved</Shown></div>
          <button type="button" onClick={() => setHide(false)}>Show</button>
        </>
      );
    }
    render(<LiveRegions><Host /></LiveRegions>);
    const region = screen.getByTestId('live-said');
    expect(region.textContent).toBe('');
    fireEvent.click(screen.getByRole('button', { name: 'Show' }));
    expect(region.textContent?.trim()).toBe('Saved');
  });

  it('says nothing outside the live regions (a component test) and still shows its words', () => {
    render(<Shown>Saved</Shown>);
    expect(screen.getByText('Saved')).toBeInTheDocument();
  });
});

// Review of #51: a line that shows what was already saved (a payoff date, a Social Security record, a full profile)
// is quiet when the page opens; a change the person makes is said.
describe('Shown quietOnMount', () => {
  const moment = () => Promise.resolve();

  it('takes the words it mounts with as already said, and says new words', async () => {
    function Host() {
      const [n, setN] = useState(1);
      return (
        <>
          <Shown quietOnMount data-testid="line">{`Paid off by March ${2030 + n}`}</Shown>
          <button type="button" onClick={() => setN((x) => x + 1)}>More</button>
        </>
      );
    }
    render(<LiveRegions><Host /></LiveRegions>);
    const region = screen.getByTestId('live-said');
    expect(screen.getByTestId('line')).toHaveTextContent('Paid off by March 2031');
    expect(region.textContent).toBe('');
    await moment();
    fireEvent.click(screen.getByRole('button', { name: 'More' }));
    expect(region.textContent?.trim()).toBe('Paid off by March 2032');
  });

  it('is read when the line mounts: a line that mounts again with quietOnMount is quiet again', () => {
    function Host() {
      const [on, setOn] = useState(true);
      return (
        <>
          {on && <Shown quietOnMount>Saved</Shown>}
          <button type="button" onClick={() => setOn((x) => !x)}>Toggle</button>
        </>
      );
    }
    render(<LiveRegions><Host /></LiveRegions>);
    fireEvent.click(screen.getByRole('button', { name: 'Toggle' }));
    fireEvent.click(screen.getByRole('button', { name: 'Toggle' }));
    expect(screen.getByTestId('live-said').textContent).toBe('');
  });

  it('with useArriving, only the lines on the first paint are quiet: a line that hides and comes back is said', async () => {
    function Host({ start }: { start: boolean }) {
      const arriving = useArriving();
      const [on, setOn] = useState(start);
      return (
        <>
          {on && <Shown quietOnMount={arriving}>Record added</Shown>}
          <button type="button" onClick={() => setOn((x) => !x)}>Toggle</button>
        </>
      );
    }
    const first = render(<LiveRegions><Host start /></LiveRegions>);
    const region = () => screen.getByTestId('live-said');
    expect(region().textContent).toBe('');
    fireEvent.click(screen.getByRole('button', { name: 'Toggle' }));
    fireEvent.click(screen.getByRole('button', { name: 'Toggle' }));
    expect(region().textContent?.trim()).toBe('Record added');
    first.unmount();

    render(<LiveRegions><Host start={false} /></LiveRegions>);
    await moment();
    fireEvent.click(screen.getByRole('button', { name: 'Toggle' }));
    expect(region().textContent?.trim()).toBe('Record added');
  });
});
