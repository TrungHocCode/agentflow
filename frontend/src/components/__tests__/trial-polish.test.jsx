import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from '../../App';
import ExecutionTracker from '../ExecutionTracker';
import NotificationBell from '../NotificationBell';
import { countLabel, sourceLabel } from '../../lib/labels';
import { listWatchlists } from '../../services/ciApi';

function unauthorized() {
  return {
    ok: false,
    status: 401,
    statusText: 'Unauthorized',
    text: async () => JSON.stringify({ error: { message: 'Authentication required.', error_id: 'abc' } }),
    json: async () => ({})
  };
}

describe('sign-in screen', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.stubGlobal('fetch', vi.fn(async () => unauthorized()));
  });
  afterEach(() => {
    localStorage.clear();
    vi.unstubAllGlobals();
  });

  it('greets a first-time visitor without claiming a session expired', async () => {
    render(<App />);
    expect(await screen.findByRole('button', { name: /sign in/i })).toBeInTheDocument();
    await waitFor(() => expect(vi.mocked(fetch)).toHaveBeenCalled());
    expect(screen.queryByText(/session has expired/i)).not.toBeInTheDocument();
  });

  it('asks for sign-in even when the backend cannot be reached', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new Error('offline'); }));
    render(<App />);
    expect(await screen.findByRole('button', { name: /sign in/i })).toBeInTheDocument();
    expect(screen.queryByText('Watchlists')).not.toBeInTheDocument();
  });

  it('still tells a signed-in user when their session expires', async () => {
    localStorage.setItem('agentflow_access_token', 'expired-token');
    localStorage.setItem('agentflow_user', JSON.stringify({ id: 'u1', email: 'a@example.com' }));
    render(<App />);
    expect(await screen.findByText(/session has expired/i)).toBeInTheDocument();
  });
});

describe('API errors', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('shows a rejected request without status codes or reference noise', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: false,
      status: 422,
      statusText: 'Unprocessable',
      text: async () => JSON.stringify({ error: { message: 'Name is required.', error_id: 'abc' } })
    })));
    await expect(listWatchlists()).rejects.toMatchObject({ message: 'Name is required.', status: 422 });
  });

  it('keeps the reference on server faults for support', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: false,
      status: 503,
      statusText: 'Unavailable',
      text: async () => JSON.stringify({ error: { message: 'Search is down.', error_id: 'abc' } })
    })));
    await expect(listWatchlists()).rejects.toMatchObject({ message: 'Search is down. (reference: abc)', status: 503 });
  });
});

describe('NotificationBell', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ items: [] }) })));
  });
  afterEach(() => vi.unstubAllGlobals());

  async function openBell() {
    render(<div><NotificationBell /><p>Outside</p></div>);
    fireEvent.click(screen.getByRole('button', { name: /notifications/i }));
    await screen.findByTestId('notification-list');
  }

  it('closes on an outside press', async () => {
    await openBell();
    fireEvent.pointerDown(screen.getByText('Outside'));
    expect(screen.queryByTestId('notification-list')).not.toBeInTheDocument();
  });

  it('closes on Escape', async () => {
    await openBell();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByTestId('notification-list')).not.toBeInTheDocument();
  });

  it('stays open when pressing inside it', async () => {
    await openBell();
    fireEvent.pointerDown(screen.getByTestId('notification-list'));
    expect(screen.getByTestId('notification-list')).toBeInTheDocument();
  });
});

describe('ExecutionTracker', () => {
  const RUN = { run_id: 'r1', status: 'completed' };

  it('summarizes collected evidence instead of dumping JSON', () => {
    render(<ExecutionTracker
      currentRun={RUN}
      plan={[{ id: 1, node: '7b66ed38-4a14-421d-a920-ad0a211172c6', description: 'Collect', status: 'done' }]}
      results={[{ task_id: 1, status: 'done', result: { schema_version: '1', documents: ['a', 'b'], claims: ['c'] } }]}
    />);
    expect(screen.getByText('Collected 2 sources and extracted 1 evidence claim.')).toBeInTheDocument();
    expect(screen.queryByText(/schema_version/)).not.toBeInTheDocument();
    expect(screen.queryByText('7b66ed38-4a14-421d-a920-ad0a211172c6')).not.toBeInTheDocument();
  });

  it('shows readable agent roles', () => {
    render(<ExecutionTracker
      currentRun={RUN}
      plan={[{ id: 1, node: 'competitive_analyst', description: 'Compare', status: 'done' }]}
      results={[]}
    />);
    expect(screen.getByText('competitive analyst')).toBeInTheDocument();
  });
});

describe('labels', () => {
  it('counts with correct plurals', () => {
    expect(countLabel(1, 'source')).toBe('1 source');
    expect(countLabel(3, 'source')).toBe('3 sources');
    expect(countLabel(0, 'source')).toBe('0 sources');
  });

  it('prefers a source name and falls back to a short id', () => {
    expect(sourceLabel({ source_label: 'Notion (pricing)', source_id: 'abcdefgh-1' })).toBe('Notion (pricing)');
    expect(sourceLabel({ source_id: 'abcdefgh-1' })).toBe('Source abcdefgh');
  });
});
