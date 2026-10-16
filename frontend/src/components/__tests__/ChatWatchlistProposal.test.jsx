import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import ChatWatchlistProposal from '../ChatWatchlistProposal';

const RECOMMENDATION = {
  products: [
    {
      name: 'Todoist',
      kind: 'competitor',
      company: 'Doist',
      official_website: 'https://todoist.com/',
      sources: [{ url: 'https://todoist.com/pricing', kind: 'pricing', provenance: 'model_knowledge', note: '' }],
      rationale: 'Direct competitor.'
    }
  ],
  suggested_goal: 'Track Todoist pricing',
  suggested_dimensions: ['pricing'],
  suggested_criteria: [],
  warnings: ['Unverified draft.']
};

describe('ChatWatchlistProposal', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn(async (url, _options) => {
      if (String(url).includes('/recommendations')) {
        return { ok: true, json: async () => RECOMMENDATION };
      }
      return { ok: true, json: async () => ({ id: 'w1' }) };
    }));
  });

  it('suggests from chat text and creates from picks', async () => {
    const onCreated = vi.fn();
    render(<ChatWatchlistProposal messages={[{ sender: 'user', content: 'I make TaskFlow' }]} onCreated={onCreated} />);
    fireEvent.click(screen.getByRole('button', { name: /suggest watchlist/i }));
    await waitFor(() => expect(screen.getByDisplayValue('https://todoist.com/pricing')).toBeInTheDocument());
    expect(screen.getByText('model_knowledge')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /create watchlist from picks/i }));
    await waitFor(() => expect(screen.getByTestId('chat-proposal-done')).toBeInTheDocument());
    expect(onCreated).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: /view in intelligence/i }));
    expect(onCreated).toHaveBeenCalledWith('w1');
  });
});
