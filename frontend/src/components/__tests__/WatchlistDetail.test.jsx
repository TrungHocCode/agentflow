import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import WatchlistDetail from '../WatchlistDetail';

const WATCHLIST = {
  id: 'w1',
  name: 'Note apps',
  description: '',
  status: 'active',
  current_revision: {
    id: 'r1',
    revision_number: 1,
    approval_status: 'unapproved',
    config: {
      goal: 'Track pricing',
      dimensions: ['pricing'],
      workflow_version_id: 'v1',
      products: [
        { id: 'p1', name: 'Notion', kind: 'competitor', sources: [{ id: 's1', url: 'https://www.notion.com/pricing', kind: 'pricing', enabled: true }] }
      ]
    }
  }
};

const FAILED_RUN = {
  run_id: 'run-failed',
  status: 'failed',
  created_at: '2026-11-06T07:44:00Z',
  error_message: 'The local model could not load because this machine is out of memory. Reference: abc.'
};

function respond(payload) {
  return { ok: true, status: 200, json: async () => payload };
}

function stubApi({ runs = [], model = { status: 'ok' } } = {}) {
  vi.stubGlobal('fetch', vi.fn(async (url, options) => {
    const target = String(url);
    if (target.endsWith('/health/model')) return respond(model);
    if (target.includes('/revisions/') && options?.method === 'POST') return respond({});
    if (target.endsWith('/revisions')) return respond([WATCHLIST.current_revision]);
    if (target.includes('/watchlists/w1/runs') && options?.method === 'POST') return respond({ run_id: 'new' });
    if (target.includes('/watchlists/w1/runs')) return respond({ items: runs });
    if (target.includes('/briefs')) return respond({ items: [] });
    if (target.includes('/schedules') || target.includes('/timeline')) return respond([]);
    if (target.endsWith('/watchlists/w1') && options?.method === 'DELETE') return { ok: true, status: 204 };
    if (target.endsWith('/watchlists/w1')) return respond(WATCHLIST);
    return respond({ items: [] });
  }));
}

function calls(fragment, method) {
  return vi.mocked(fetch).mock.calls.filter(
    ([url, options]) => String(url).includes(fragment) && options?.method === method
  );
}

describe('WatchlistDetail', () => {
  afterEach(() => vi.unstubAllGlobals());
  beforeEach(() => stubApi());

  it('says that running an unapproved revision approves it, and lists what is approved', async () => {
    render(<WatchlistDetail watchlistId="w1" onBack={() => {}} />);
    expect(await screen.findByRole('button', { name: /approve & run/i })).toBeInTheDocument();
    expect(screen.getByText('https://www.notion.com/pricing')).toBeInTheDocument();
  });

  it('explains a failed run and lets the user open it', async () => {
    stubApi({ runs: [FAILED_RUN] });
    const onOpenRun = vi.fn();
    render(<WatchlistDetail watchlistId="w1" onBack={() => {}} onOpenRun={onOpenRun} />);

    expect(await screen.findByText(/out of memory/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /view/i }));
    expect(onOpenRun).toHaveBeenCalledWith(FAILED_RUN);
  });

  it('does not approve or start when the model is unavailable', async () => {
    stubApi({ model: { status: 'unavailable', code: 'model_unreachable', message: 'Ollama could not be reached.' } });
    render(<WatchlistDetail watchlistId="w1" onBack={() => {}} />);
    fireEvent.click(await screen.findByRole('button', { name: /approve & run/i }));

    expect(await screen.findByText('Ollama could not be reached.')).toBeInTheDocument();
    expect(calls('/approval', 'POST')).toHaveLength(0);
    expect(calls('/watchlists/w1/runs', 'POST')).toHaveLength(0);
  });

  it('still runs when the availability check itself cannot answer', async () => {
    const api = fetch;
    vi.stubGlobal('fetch', vi.fn(async (url, options) => {
      if (String(url).endsWith('/health/model')) throw new Error('network');
      return api(url, options);
    }));
    render(<WatchlistDetail watchlistId="w1" onBack={() => {}} />);
    fireEvent.click(await screen.findByRole('button', { name: /approve & run/i }));

    await waitFor(() => expect(calls('/watchlists/w1/runs', 'POST')).toHaveLength(1));
  });

  it('archives only after confirmation', async () => {
    const onBack = vi.fn();
    render(<WatchlistDetail watchlistId="w1" onBack={onBack} />);
    await screen.findByRole('button', { name: /approve & run/i });
    fireEvent.click(screen.getByRole('tab', { name: /settings/i }));
    fireEvent.click(await screen.findByRole('button', { name: /^archive$/i }));
    expect(calls('/watchlists/w1', 'DELETE')).toHaveLength(0);

    const dialog = await screen.findByRole('dialog');
    fireEvent.click(dialog.querySelector('button[autofocus], button:last-child'));
    await waitFor(() => expect(onBack).toHaveBeenCalled());
    expect(calls('/watchlists/w1', 'DELETE')).toHaveLength(1);
  });
});
