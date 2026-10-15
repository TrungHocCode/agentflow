import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import TimelineAndAsk from '../TimelineAndAsk';

describe('TimelineAndAsk', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn(async (url) => {
      if (String(url).includes('/ask')) {
        return {
          ok: true,
          json: async () => ({
            question: 'When did Pricing change?',
            intent: 'temporal',
            verdict: 'answered',
            text: 'On 2026-10-09: modified in Pricing.',
            citations: [{ snapshot_id: 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee', start_offset: 0, end_offset: 10, excerpt: 'Pro' }]
          })
        };
      }
      return { ok: true, json: async () => [] };
    }));
  });

  it('lists events and answers with cited evidence', async () => {
    render(<TimelineAndAsk watchlistId="w1" />);
    await waitFor(() => expect(screen.getByLabelText(/ask history/i)).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText(/ask history/i), { target: { value: 'When did Pricing change?' } });
    fireEvent.click(screen.getByRole('button', { name: /^ask$/i }));
    await waitFor(() => expect(screen.getByTestId('history-answer')).toBeInTheDocument());
    expect(screen.getByText(/answered \(temporal\)/i)).toBeInTheDocument();
    expect(screen.getByText(/evidence 1/i)).toBeInTheDocument();
  });
});
