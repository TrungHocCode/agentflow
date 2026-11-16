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
      provenance: 'web_search',
      suggested: true
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
    vi.stubGlobal('fetch', vi.fn(async (url, options = {}) => {
      const target = String(url);
      if (target.includes('/discover-competitors')) return response(COMPETITORS);
      if (target.includes('/discover-sources')) return response(SOURCES);
      if (target.includes('/health/model')) return response({ status: 'ok' });
      if (target.endsWith('/watchlists/w1') && !options.method) {
        return response({ id: 'w1', name: 'w', current_revision: {
          id: 'r1', approval_status: 'approved', config: { workflow_version_id: 'v1' }
        } });
      }
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

    // Approval needs the user's own product, so creating waits until it is named.
    expect(screen.getByRole('status')).toHaveTextContent(/your own product/i);
    expect(screen.getByRole('button', { name: /create watchlist from picks/i })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Your product'), { target: { value: 'TaskFlow' } });
    fireEvent.click(screen.getByRole('button', { name: /^add$/i }));

    fireEvent.click(screen.getByRole('button', { name: /create watchlist from picks/i }));
    await waitFor(() => expect(screen.getByTestId('chat-proposal-done')).toBeInTheDocument());
    const created = JSON.parse(fetch.mock.calls.find(([url, options]) => String(url).endsWith('/watchlists')
      && options?.method === 'POST')[1].body);
    expect(created.config.products.map((item) => [item.name, item.kind])).toEqual(
      [['Todoist', 'competitor'], ['TaskFlow', 'own']]
    );
    expect(created.config.comparison_criteria).toEqual(
      [{ field: 'pricing', objective: 'Track pricing changes', importance: 'medium' }]
    );

    fireEvent.click(screen.getByRole('button', { name: /approve & run/i }));
    expect(await screen.findByText(/first run started/i)).toBeInTheDocument();
    expect(fetch.mock.calls.some(([url, options]) => String(url).endsWith('/watchlists/w1/runs')
      && options?.method === 'POST')).toBe(true);
    expect(onCreated).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: /open watchlist/i }));
    expect(onCreated).toHaveBeenCalledWith('w1');
  });

  it('shows each website and flags a URL typed under the wrong same-named product', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => response({ ...COMPETITORS, products: [
      { ...COMPETITORS.products[0], name: 'Craft', official_website: 'https://craft.io/', suggested: true },
      { ...COMPETITORS.products[0], name: 'InkPad', kind: 'own', official_website: null, suggested: true }
    ] })));
    render(<ChatWatchlistProposal messages={[{ sender: 'user', content: 'I make InkPad' }]} onCreated={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: /find competitors/i }));
    expect(await screen.findByText('craft.io')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /add source url/i }));
    fireEvent.change(screen.getByLabelText('Source URL for Craft'), { target: { value: 'https://www.craft.do/pricing' } });
    expect(screen.getByRole('status')).toHaveTextContent("https://www.craft.do/pricing to be on Craft's site (craft.io)");
    expect(screen.getByRole('button', { name: /create watchlist from picks/i })).toBeDisabled();

    fireEvent.change(screen.getByLabelText('Source URL for Craft'), { target: { value: 'https://www.craft.io/pricing' } });
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it('lets the user type a source URL when search finds none', async () => {
    render(<ChatWatchlistProposal messages={[{ sender: 'user', content: 'I make TaskFlow' }]} onCreated={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: /find competitors/i }));
    await waitFor(() => expect(screen.getByLabelText('Track Todoist')).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText('Your product'), { target: { value: 'TaskFlow' } });
    fireEvent.click(screen.getByRole('button', { name: /^add$/i }));
    expect(screen.getByRole('status')).toHaveTextContent(/a source url for a competitor/i);

    fireEvent.click(screen.getByRole('button', { name: /add source url/i }));
    fireEvent.change(screen.getByLabelText('Source URL for Todoist'), { target: { value: 'https://todoist.com/pricing' } });
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /create watchlist from picks/i })).toBeEnabled();
  });

  it('leaves unnamed candidates unpicked and says when no sources were found', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url) => response(String(url).includes('/discover-sources')
      ? { 'https://todoist.com/': [] }
      : { ...COMPETITORS, products: [
        { ...COMPETITORS.products[0], name: 'Named', official_website: 'https://todoist.com/' },
        { ...COMPETITORS.products[0], name: 'Unnamed', official_website: 'https://blog.example/', suggested: false }
      ] })));
    render(<ChatWatchlistProposal messages={[{ sender: 'user', content: 'I make TaskFlow' }]} onCreated={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: /find competitors/i }));

    await waitFor(() => expect(screen.getByLabelText('Track Named')).toBeChecked());
    expect(screen.getByLabelText('Track Unnamed')).not.toBeChecked();
    fireEvent.click(screen.getByRole('button', { name: /find source urls/i }));
    expect(await screen.findByText(/no source pages were found/i)).toBeInTheDocument();
  });
});
