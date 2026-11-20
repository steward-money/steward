import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { planFixture } from './fixtures';
import { SaveBar } from './SaveBar';

afterEach(() => vi.unstubAllGlobals());
const plan = planFixture().plan;
const now = new Date(2026, 9, 14, 22, 0);
const base = { plan, dirty: false, busy: false, error: null, note: null, onSave: () => {}, onReset: () => {}, now };

describe('SaveBar', () => {
  it('shows nothing when the levers match the saved plan', () => {
    const { container } = render(<SaveBar {...base} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('says "Plan not updated", with Reset and Update the plan, and nothing else', () => {
    const onSave = vi.fn();
    const onReset = vi.fn();
    render(<SaveBar {...base} dirty onSave={onSave} onReset={onReset} />);
    const bar = screen.getByTestId('savebar');
    expect(bar).toHaveAccessibleName('Plan not updated');
    expect(bar.querySelector('.savebar__text')).toHaveTextContent(/^Plan not updated$/);
    fireEvent.click(screen.getByRole('button', { name: 'Reset' }));
    fireEvent.click(screen.getByRole('button', { name: 'Update the plan' }));
    expect(onReset).toHaveBeenCalled();
    expect(onSave).toHaveBeenCalled();
  });

  it('offers a first save when nothing is saved yet', () => {
    render(<SaveBar {...base} plan={null} />);
    expect(screen.getByTestId('savebar')).toHaveTextContent('No plan is saved yet. Save one to show these ages on Home.');
    expect(screen.getByRole('button', { name: 'Save the plan' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Reset' })).toBeNull();
    expect(screen.getByTestId('savebar')).toHaveAccessibleName('No plan is saved yet. Save one to show these ages on Home.');
  });

  it('keeps the confirmation after a save', () => {
    const at = new Date(2026, 9, 14, 21, 14).toISOString();
    render(<SaveBar {...base} note={{ planId: 7, at, day: '2026-10-14', by: 'JR', previous: { name: 'Sam', saved_day: '2026-10-12' } }} />);
    const done = screen.getByTestId('savebar-done');
    expect(done).not.toHaveAttribute('role');      // said through the page's polite region (issue #51)
    expect(done).toHaveTextContent('Plan updated.');
    expect(done).toHaveTextContent(/Saved by JR today at 9:14 pm\. The previous version, saved by Sam on Oct 12, is in Earlier versions\./);
  });

  it('pins itself when scrolled out of view', () => {
    let fire: (entries: { isIntersecting: boolean }[]) => void = () => {};
    vi.stubGlobal('IntersectionObserver', class {
      constructor(cb: (e: { isIntersecting: boolean }[]) => void) { fire = cb; }
      observe() {}
      disconnect() {}
    });
    render(<SaveBar {...base} dirty />);
    expect(screen.getByTestId('savebar')).not.toHaveClass('savebar--pinned');
    act(() => fire([{ isIntersecting: false }]));
    expect(screen.getByTestId('savebar')).toHaveClass('savebar--pinned');
    act(() => fire([{ isIntersecting: true }]));
    expect(screen.getByTestId('savebar')).not.toHaveClass('savebar--pinned');
  });
});
