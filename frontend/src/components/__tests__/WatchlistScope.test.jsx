import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import WatchlistScope from '../WatchlistScope';

const WATCHLIST = {
  id: 'w1',
  name: 'Note apps',
  description: '',
  current_revision: {
    id: 'r1',
    config: {
      goal: 'Track pricing',
      dimensions: ['pricing', 'release_notes'],
      workflow_version_id: 'v1',
      products: [
        {
          id: 'p1',
          name: 'Notion',
          kind: 'competitor',
          official_website: 'https://notion.com/',
          profile_version_id: 'pv1',
          profile: { facts: [], target_segments: [] },
          sources: [
            { id: 's1', url: 'https://www.notion.com/pricing', kind: 'pricing', enabled: true },
            { id: 's2', url: 'https://www.notion.com/releases', kind: 'release_notes', enabled: true }
          ]
        }
      ]
    }
  }
};

function patchBody() {
  const call = vi.mocked(fetch).mock.calls.find(([, options]) => options?.method === 'PATCH');
  return JSON.parse(call[1].body);
}

describe('WatchlistScope', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ({ id: 'w1' }) })));
  });
  afterEach(() => vi.unstubAllGlobals());

  it('lists the exact sources a run will collect', () => {
    render(<WatchlistScope watchlist={WATCHLIST} onSaved={() => {}} />);
    expect(screen.getByText('Notion')).toBeInTheDocument();
    expect(screen.getByText('https://www.notion.com/pricing')).toBeInTheDocument();
    expect(screen.getByText('release notes')).toBeInTheDocument();
  });

  it('starts a new source identity only when its URL changes', async () => {
    const onSaved = vi.fn();
    render(<WatchlistScope watchlist={WATCHLIST} onSaved={onSaved} />);
    fireEvent.click(screen.getByRole('button', { name: /edit/i }));
    fireEvent.change(screen.getByDisplayValue('https://www.notion.com/pricing'), {
      target: { value: 'https://www.notion.com/plans' }
    });
    fireEvent.click(screen.getByRole('button', { name: /save changes/i }));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());

    const body = patchBody();
    expect(body.expected_revision_id).toBe('r1');
    const product = body.config.products[0];
    expect(product.id).toBe('p1');
    expect(product.profile_version_id).toBe('pv1');
    expect(product.sources).toEqual([
      { url: 'https://www.notion.com/plans', kind: 'pricing', enabled: true },
      { id: 's2', url: 'https://www.notion.com/releases', kind: 'release_notes', enabled: true }
    ]);
    expect(body.config.workflow_version_id).toBe('v1');
  });

  it('drops blank sources and blocks saving a nameless product', async () => {
    render(<WatchlistScope watchlist={WATCHLIST} onSaved={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: /edit/i }));
    fireEvent.click(screen.getByRole('button', { name: /add source/i }));
    fireEvent.change(screen.getByLabelText('Product name'), { target: { value: ' ' } });
    expect(screen.getByRole('button', { name: /save changes/i })).toBeDisabled();

    fireEvent.change(screen.getByLabelText('Product name'), { target: { value: 'Notion' } });
    fireEvent.click(screen.getByRole('button', { name: /save changes/i }));
    await waitFor(() => expect(patchBody().config.products[0].sources).toHaveLength(2));
  });

  it('offers only source kinds within the watchlist dimensions', () => {
    const features = {
      ...WATCHLIST,
      current_revision: {
        ...WATCHLIST.current_revision,
        config: {
          ...WATCHLIST.current_revision.config,
          dimensions: ['features'],
          products: [{ id: 'p1', name: 'Notion', kind: 'competitor', sources: [] }]
        }
      }
    };
    render(<WatchlistScope watchlist={features} onSaved={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: /edit/i }));
    fireEvent.click(screen.getByRole('button', { name: /add source/i }));

    const select = screen.getByLabelText('Source kind');
    expect(select).toHaveValue('features');
    expect([...select.options].map((option) => option.value)).toEqual(['features']);
  });

  it('keeps the draft open and shows why when saving fails', async () => {
    vi.mocked(fetch).mockResolvedValueOnce({
      ok: false,
      status: 409,
      text: async () => JSON.stringify({ error: { message: 'The current watchlist revision has changed.' } })
    });
    render(<WatchlistScope watchlist={WATCHLIST} onSaved={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: /edit/i }));
    fireEvent.click(screen.getByRole('button', { name: /save changes/i }));

    await waitFor(() => expect(screen.getByText(/revision has changed/i)).toBeInTheDocument());
    expect(screen.getByTestId('watchlist-scope-edit')).toBeInTheDocument();
  });
});
