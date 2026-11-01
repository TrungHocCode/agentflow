import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import WatchlistSetup from '../WatchlistSetup';

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
    },
    {
      name: 'ClickUp',
      kind: 'competitor',
      company: '',
      official_website: 'https://clickup.com/',
      sources: [],
      rationale: 'Found via web search.',
      provenance: 'model_knowledge'
    }
  ],
  suggested_goal: '',
  suggested_dimensions: ['pricing'],
  suggested_criteria: [],
  warnings: []
};

const SOURCES = {
  'https://todoist.com/': [
    { url: 'https://todoist.com/pricing', kind: 'pricing', provenance: 'web_search', note: 'Pricing' }
  ],
  'https://clickup.com/': [
    { url: 'https://clickup.com/pricing', kind: 'pricing', provenance: 'web_search', note: 'Pricing' }
  ]
};

function response(payload) {
  return { ok: true, json: async () => payload };
}

function nextButton() {
  return screen.getByRole('button', { name: /^next$/i });
}

describe('setup wizard story', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn(async (url) => {
      const target = String(url);
      if (target.includes('/discover-competitors')) return response(COMPETITORS);
      if (target.includes('/discover-sources')) return response(SOURCES);
      return response({ id: 'w1' });
    }));
  });

  it('gates every step until required input exists', () => {
    render(<WatchlistSetup onCreated={() => {}} onCancel={() => {}} />);

    expect(screen.getByTestId('wizard-step-1')).toHaveAttribute('data-state', 'active');
    expect(nextButton()).toBeDisabled();

    fireEvent.change(screen.getByLabelText(/describe your product/i), { target: { value: 'I make TaskFlow' } });
    expect(nextButton()).toBeEnabled();
    fireEvent.click(nextButton());

    expect(screen.getByTestId('wizard-step-1')).toHaveAttribute('data-state', 'done');
    expect(screen.getByTestId('wizard-step-2')).toHaveAttribute('data-state', 'active');
    expect(screen.getByTestId('setup-step-2')).toBeInTheDocument();
    expect(nextButton()).toBeDisabled();

    fireEvent.click(screen.getByRole('button', { name: /add competitor/i }));
    expect(nextButton()).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Product name'), { target: { value: 'Rival' } });
    expect(nextButton()).toBeEnabled();
    fireEvent.click(nextButton());

    expect(screen.getByTestId('wizard-step-3')).toHaveAttribute('data-state', 'active');
    expect(screen.getByTestId('setup-step-3')).toBeInTheDocument();
    fireEvent.click(nextButton());

    expect(screen.getByTestId('wizard-step-4')).toHaveAttribute('data-state', 'active');
    expect(screen.getByRole('button', { name: /create watchlist/i })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Watchlist name'), { target: { value: 'Rival watch' } });
    expect(screen.getByRole('button', { name: /create watchlist/i })).toBeEnabled();
  });

  it('lets the user tick competitors and only discovers their sources', async () => {
    render(<WatchlistSetup onCreated={() => {}} onCancel={() => {}} />);
    fireEvent.change(screen.getByLabelText(/describe your product/i), { target: { value: 'I make TaskFlow' } });
    fireEvent.click(nextButton());
    fireEvent.click(screen.getByRole('button', { name: /find competitors/i }));

    await waitFor(() => expect(screen.getByLabelText('Track ClickUp')).toBeChecked());
    expect(screen.getByLabelText('Track Todoist')).toBeChecked();
    expect(screen.getAllByText('model_knowledge')[0]).toHaveAttribute('title', 'may be stale — verify');

    fireEvent.click(screen.getByLabelText('Track ClickUp'));
    expect(screen.getByLabelText('Track ClickUp')).not.toBeChecked();
    fireEvent.click(nextButton());
    fireEvent.click(screen.getByRole('button', { name: /find source urls/i }));

    await waitFor(() => expect(screen.getByDisplayValue('https://todoist.com/pricing')).toBeInTheDocument());
    expect(screen.queryByDisplayValue('https://clickup.com/pricing')).not.toBeInTheDocument();
  });

  it('creates the watchlist with the existing payload shape', async () => {
    render(<WatchlistSetup onCreated={() => {}} onCancel={() => {}} />);
    fireEvent.change(screen.getByLabelText(/describe your product/i), { target: { value: 'I make TaskFlow' } });
    fireEvent.click(nextButton());
    fireEvent.click(screen.getByRole('button', { name: /find competitors/i }));
    await waitFor(() => expect(screen.getByDisplayValue('Todoist')).toBeInTheDocument());
    fireEvent.click(nextButton());
    fireEvent.click(screen.getByRole('button', { name: /find source urls/i }));
    await waitFor(() => expect(screen.getByDisplayValue('https://todoist.com/pricing')).toBeInTheDocument());
    fireEvent.click(nextButton());

    fireEvent.change(screen.getByLabelText('Watchlist name'), { target: { value: 'TaskFlow pricing watch' } });
    fireEvent.click(screen.getByRole('button', { name: /create watchlist/i }));
    await waitFor(() => expect(screen.getByTestId('setup-created')).toBeInTheDocument());

    const createCall = vi.mocked(fetch).mock.calls.find(
      ([url, options]) => String(url).endsWith('/watchlists') && options?.method === 'POST'
    );
    expect(JSON.parse(createCall[1].body)).toEqual({
      name: 'TaskFlow pricing watch',
      description: '',
      config: {
        goal: 'I make TaskFlow',
        dimensions: ['pricing'],
        workflow_version_id: null,
        comparison_criteria: [
          { field: 'price', objective: 'Track public pricing changes', importance: 'high' }
        ],
        products: [
          {
            name: 'Todoist',
            kind: 'competitor',
            company: 'Doist',
            official_website: 'https://todoist.com/',
            profile: { facts: [], target_segments: [] },
            sources: [{ url: 'https://todoist.com/pricing', kind: 'pricing', enabled: true }]
          },
          {
            name: 'ClickUp',
            kind: 'competitor',
            company: '',
            official_website: 'https://clickup.com/',
            profile: { facts: [], target_segments: [] },
            sources: [{ url: 'https://clickup.com/pricing', kind: 'pricing', enabled: true }]
          }
        ]
      }
    });
  });
});
