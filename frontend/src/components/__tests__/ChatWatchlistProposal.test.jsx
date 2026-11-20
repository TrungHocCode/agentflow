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

const PRODUCT = { name: 'TaskFlow', description: 'team task manager', source: 'default', my_product_id: 'p1' };

function setupOf(recommendation, product = PRODUCT) {
  return { product, recommendation };
}

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

  it('shows the follow-up question and searches again with the earlier request and the answer', async () => {
    const asked = { products: [], warnings: [], follow_up_question: 'What does NoteNest do, and for whom?' };
    render(<ChatWatchlistProposal
      setup={setupOf(asked, { name: '', description: '', source: 'message', my_product_id: null })}
      messages={[
        { sender: 'user', content: 'I make NoteNest. Watch my competitors.' },
        { sender: 'assistant', content: 'Click Find competitors.' },
        { sender: 'user', content: 'It is a markdown note-taking app for Mac.' }
      ]}
      onCreated={() => {}}
    />);
    expect(screen.getByRole('status')).toHaveTextContent('What does NoteNest do');
    expect(screen.getByTestId('chat-proposal-using')).toHaveTextContent('Using: your message');
    expect(screen.queryByText(/unverified draft/i)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /search again/i }));
    await waitFor(() => expect(fetch.mock.calls.some(([url]) => String(url).includes('/discover-competitors'))).toBe(true));
    const body = JSON.parse(fetch.mock.calls.find(([url]) => String(url).includes('/discover-competitors'))[1].body);
    expect(body.prompt).toBe('I make NoteNest. Watch my competitors.\nIt is a markdown note-taking app for Mac.');
  });

  it('discovers competitors, then source URLs, and creates from picks', async () => {
    const onCreated = vi.fn();
    render(<ChatWatchlistProposal
      setup={setupOf(COMPETITORS)}
      messages={[{ sender: 'user', content: 'I make TaskFlow' }]}
      onCreated={onCreated}
    />);
    // The competitors are preloaded: no click is needed to search.
    expect(screen.getByTestId('chat-proposal-using')).toHaveTextContent('Using: TaskFlow (default)');
    expect(screen.getByLabelText('Track Todoist')).toBeInTheDocument();
    expect(screen.getAllByText('web_search').length).toBeGreaterThan(0);
    expect(fetch.mock.calls.some(([url]) => String(url).includes('/discover-competitors'))).toBe(false);

    fireEvent.click(screen.getByRole('button', { name: /find source urls/i }));
    await waitFor(() => expect(screen.getByDisplayValue('https://todoist.com/pricing')).toBeInTheDocument());
    expect(screen.getByLabelText('Track source https://todoist.com/pricing')).toBeInTheDocument();
    expect(fetch.mock.calls.filter(([url]) => String(url).includes('/discover-sources'))).toHaveLength(1);

    // Approval needs the user's own product, so creating waits until it is named.
    expect(screen.getByRole('status')).toHaveTextContent(/your own product/i);
    expect(screen.getByRole('button', { name: /create watchlist from picks/i })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Your product'), { target: { value: 'TaskFlow' } });
    fireEvent.click(screen.getByRole('button', { name: /^add$/i }));

    fireEvent.click(screen.getByRole('button', { name: /create watchlist from picks/i }));
    await waitFor(() => expect(screen.getByTestId('chat-proposal-done')).toBeInTheDocument());
    const created = JSON.parse(fetch.mock.calls.find(([url, options]) => String(url).endsWith('/watchlists')
      && options?.method === 'POST')[1].body);
    expect(created.my_product_id).toBe('p1');
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
    const found = { ...COMPETITORS, products: [
      { ...COMPETITORS.products[0], name: 'Craft', official_website: 'https://craft.io/', suggested: true },
      { ...COMPETITORS.products[0], name: 'InkPad', kind: 'own', official_website: null, suggested: true }
    ] };
    render(<ChatWatchlistProposal
      setup={setupOf(found)}
      messages={[{ sender: 'user', content: 'I make InkPad' }]}
      onCreated={() => {}}
    />);
    expect(await screen.findByText('craft.io')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /add source url/i }));
    fireEvent.change(screen.getByLabelText('Source URL for Craft'), { target: { value: 'https://www.craft.do/pricing' } });
    expect(screen.getByRole('status')).toHaveTextContent("https://www.craft.do/pricing to be on Craft's site (craft.io)");
    expect(screen.getByRole('button', { name: /create watchlist from picks/i })).toBeDisabled();

    fireEvent.change(screen.getByLabelText('Source URL for Craft'), { target: { value: 'https://www.craft.io/pricing' } });
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it('lets the user type a source URL when search finds none', async () => {
    render(<ChatWatchlistProposal
      setup={setupOf(COMPETITORS)}
      messages={[{ sender: 'user', content: 'I make TaskFlow' }]}
      onCreated={() => {}}
    />);
    fireEvent.change(screen.getByLabelText('Your product'), { target: { value: 'TaskFlow' } });
    fireEvent.click(screen.getByRole('button', { name: /^add$/i }));
    expect(screen.getByRole('status')).toHaveTextContent(/a source url for a competitor/i);

    fireEvent.click(screen.getByRole('button', { name: /add source url/i }));
    fireEvent.change(screen.getByLabelText('Source URL for Todoist'), { target: { value: 'https://todoist.com/pricing' } });
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /create watchlist from picks/i })).toBeEnabled();
  });

  it('leaves unnamed candidates unpicked and says when no sources were found', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => response({ 'https://todoist.com/': [] })));
    const found = { ...COMPETITORS, products: [
      { ...COMPETITORS.products[0], name: 'Named', official_website: 'https://todoist.com/' },
      { ...COMPETITORS.products[0], name: 'Unnamed', official_website: 'https://blog.example/', suggested: false }
    ] };
    render(<ChatWatchlistProposal
      setup={setupOf(found)}
      messages={[{ sender: 'user', content: 'I make TaskFlow' }]}
      onCreated={() => {}}
    />);

    expect(screen.getByLabelText('Track Named')).toBeChecked();
    expect(screen.getByLabelText('Track Unnamed')).not.toBeChecked();
    fireEvent.click(screen.getByRole('button', { name: /find source urls/i }));
    expect(await screen.findByText(/no source page was found/i)).toBeInTheDocument();
    const asked = JSON.parse(fetch.mock.calls.find(([url]) => String(url).includes('/discover-sources'))[1].body);
    expect(asked.domains).toEqual(['https://todoist.com/']);
  });

  it('saves a product described in the message first, then links it to the watchlist', async () => {
    fetch.mockImplementation(async (url, options = {}) => (String(url).endsWith('/my-products') && options.method === 'POST'
      ? response({ id: 'saved-1' })
      : response({ id: 'w1' })));
    const found = { ...COMPETITORS, products: [
      { ...COMPETITORS.products[0], official_website: 'https://todoist.com/' },
      { ...COMPETITORS.products[0], name: 'NoteNest', kind: 'own', official_website: null, suggested: true }
    ] };
    render(<ChatWatchlistProposal
      setup={setupOf(found, { name: 'NoteNest', description: 'markdown app', source: 'message', my_product_id: null })}
      messages={[{ sender: 'user', content: 'I make NoteNest, a markdown app' }]}
      onCreated={() => {}}
    />);
    expect(screen.getByTestId('chat-proposal-using')).toHaveTextContent('Using: NoteNest (from your message)');
    expect(screen.getByLabelText('Save as my product')).toBeChecked();
    fireEvent.click(screen.getByRole('button', { name: /add source url/i }));
    fireEvent.change(screen.getByLabelText('Source URL for Todoist'), { target: { value: 'https://todoist.com/pricing' } });
    fireEvent.click(screen.getByRole('button', { name: /create watchlist from picks/i }));
    await waitFor(() => expect(screen.getByTestId('chat-proposal-done')).toBeInTheDocument());

    const posts = fetch.mock.calls.filter(([, options]) => options?.method === 'POST').map(([url]) => String(url));
    expect(posts.findIndex((url) => url.endsWith('/my-products')))
      .toBeLessThan(posts.findIndex((url) => url.endsWith('/watchlists')));
    const created = JSON.parse(fetch.mock.calls.find(([url, options]) => String(url).endsWith('/watchlists')
      && options?.method === 'POST')[1].body);
    expect(created.my_product_id).toBe('saved-1');
  });

  it('retries a failed search from the card', async () => {
    render(<ChatWatchlistProposal
      setup={{ product: null, recommendation: null, failed: true }}
      messages={[{ sender: 'user', content: 'Find my competitors' }]}
      onCreated={() => {}}
    />);
    expect(screen.getByRole('status')).toHaveTextContent(/search failed/i);
    fireEvent.click(screen.getByRole('button', { name: /search again/i }));
    expect(await screen.findByLabelText('Track Todoist')).toBeInTheDocument();
  });
});
