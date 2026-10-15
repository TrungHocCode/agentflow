import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import NotificationBell from '../NotificationBell';
import ScheduleSection from '../ScheduleSection';

describe('NotificationBell', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true,
      json: async () => ({
        items: [{ id: 'n1', title: 'Scheduled run started', body: '', read_at: null }],
        pagination: { limit: 20, offset: 0, total: 1 }
      })
    })));
  });

  it('shows unread count and marks items read', async () => {
    render(<NotificationBell />);
    await waitFor(() => expect(screen.getByTestId('notification-count')).toHaveTextContent('1'));
    fireEvent.click(screen.getByRole('button', { name: /notifications/i }));
    await waitFor(() => expect(screen.getByTestId('notification-list')).toBeInTheDocument());
  });
});

describe('ScheduleSection', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => [] })));
  });

  it('renders cadence form with weekday for weekly', async () => {
    render(<ScheduleSection watchlistId="w1" />);
    await waitFor(() => expect(screen.getByLabelText(/cadence/i)).toBeInTheDocument());
    expect(screen.getByLabelText(/day of week/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /add schedule/i })).toBeInTheDocument();
  });
});
