import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import ChatWatchlistProposal from '../ChatWatchlistProposal';

const COMPETITORS = {
  products: [
    {
      name: 'Todoist',
      kind: 'competitor',
      company: 'Doist',
      official_website: 'https://todoist.com/',
      sources: [],
      rationale: 'Found via web search.',
      provenance: 'web_search'
    }
  ],
  suggested_goal: 'Track Todoist pricing',
  suggested_dimensions: ['pricing'],
  suggested_criteria: [],
  warnings: ['Unverified search results.']
};

const SOURCES = {
  'https://todoist.com/': [
    { url: 'https://todoist.com/pricing', kind: 'pricing', provenance: 'web_search', note: 'Pricing' }
  ]
};

function response(payload) {
  return { ok: true, json: async () => payload };
}

describe('ChatWatchlistProposal', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn(async (url) => {
      const target = String(url);
      if (target.includes('/discover-competitors')) return response(COMPETITORS);
      if (target.includes('/discover-sources')) return response(SOURCES);
      return response({ id: 'w1' });
    }));
  });

  it('discovers competitors, then source URLs, and creates from picks', async () => {
    const onCreated = vi.fn();
    render(<ChatWatchlistProposal messages={[{ sender: 'user', content: 'I make TaskFlow' }]} onCreated={onCreated} />);
    fireEvent.click(screen.getByRole('button', { name: /find competitors/i }));
    await waitFor(() => expect(screen.getByLabelText('Track Todoist')).toBeInTheDocument());
    expect(screen.getAllByText('web_search').length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole('button', { name: /find source urls/i }));
    await waitFor(() => expect(screen.getByDisplayValue('https://todoist.com/pricing')).toBeInTheDocument());
    expect(screen.getByLabelText('Track source https://todoist.com/pricing')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /create watchlist from picks/i }));
    await waitFor(() => expect(screen.getByTestId('chat-proposal-done')).toBeInTheDocument());
    expect(onCreated).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: /view in intelligence/i }));
    expect(onCreated).toHaveBeenCalledWith('w1');
  });
});
