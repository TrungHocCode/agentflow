import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from '../../App';

const CONVERSATION = { id: 'c1', title: 'Old chat', status: 'active', updated_at: '2026-11-06T07:00:00Z' };

function respond(payload) {
  return { ok: true, status: 200, json: async () => payload, text: async () => JSON.stringify(payload) };
}

function deletes() {
  return vi.mocked(fetch).mock.calls.filter(([url, options]) => (
    String(url).includes('/conversations/c1') && options?.method === 'DELETE'
  ));
}

describe('deleting a chat from the recent list', () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem('agentflow_access_token', 'token');
    localStorage.setItem('agentflow_user', JSON.stringify({ id: 'u1', email: 'a@example.com' }));
    vi.stubGlobal('fetch', vi.fn(async (url, options) => {
      const target = String(url);
      if (options?.method === 'DELETE') return { ok: true, status: 204, json: async () => null, text: async () => '' };
      if (target.includes('/conversations')) return respond([CONVERSATION]);
      if (target.includes('/runs')) return respond([]);
      if (target.includes('/catalog')) return respond([]);
      return respond({ items: [] });
    }));
  });

  afterEach(() => {
    localStorage.clear();
    vi.unstubAllGlobals();
  });

  async function openDeleteDialog() {
    render(<App />);
    fireEvent.click(await screen.findByRole('button', { name: /^assistant$/i }));
    fireEvent.click(await screen.findByRole('button', { name: /delete chat old chat/i }));
    return screen.findByRole('dialog');
  }

  it('asks before deleting and keeps the chat when cancelled', async () => {
    const dialog = await openDeleteDialog();

    expect(deletes()).toHaveLength(0);
    fireEvent.click(within(dialog).getByRole('button', { name: /cancel/i }));

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(deletes()).toHaveLength(0);
  });

  it('deletes after confirmation', async () => {
    const dialog = await openDeleteDialog();

    fireEvent.click(within(dialog).getByRole('button', { name: /^delete$/i }));

    await waitFor(() => expect(deletes()).toHaveLength(1));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });
});
