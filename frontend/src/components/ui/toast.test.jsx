import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it } from 'vitest';
import { Toaster, createToastManager } from './toast';

afterEach(cleanup);

describe('notification surface', () => {
  it('shows dismissible feedback without opening a blocking dialog', async () => {
    const manager = createToastManager();
    render(<Toaster toastManager={manager} />);
    act(() => { manager.add({ title: 'Research complete', type: 'success', timeout: 0 }); });
    expect(await screen.findByText('Research complete')).toBeTruthy();
    expect(screen.getByRole('dialog').getAttribute('aria-modal')).toBe('false');
    await userEvent.click(screen.getByLabelText('Close toast'));
    await waitFor(() => expect(screen.queryByText('Research complete')).toBeNull());
  });

  it('uses a stable id to update a notice rather than duplicate it', async () => {
    const manager = createToastManager();
    render(<Toaster toastManager={manager} />);
    act(() => {
      manager.add({ id: 'run-1', title: 'Research complete', timeout: 0 });
      manager.add({ id: 'run-1', title: 'Research complete', timeout: 0 });
    });
    expect(await screen.findAllByText('Research complete')).toHaveLength(1);
  });
});
