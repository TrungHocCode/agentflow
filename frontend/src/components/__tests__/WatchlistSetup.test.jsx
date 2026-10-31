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
      provenance: 'web_search'
    }
  ],
  suggested_goal: '',
  suggested_dimensions: ['pricing'],
  suggested_criteria: [],
  warnings: ['Search results are unverified.']
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

describe('WatchlistSetup wizard', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn(async (url) => {
      const target = String(url);
      if (target.includes('/discover-competitors')) return response(COMPETITORS);
      if (target.includes('/discover-sources')) return response(SOURCES);
      return response({ id: 'w1' });
    }));
  });

  it('runs the two discovery steps and marks candidates unverified', async () => {
    render(<WatchlistSetup onCreated={() => {}} onCancel={() => {}} />);
    fireEvent.change(screen.getByLabelText(/describe your product/i), { target: { value: 'I make TaskFlow' } });
    fireEvent.click(screen.getByRole('button', { name: /find competitors/i }));
    await waitFor(() => expect(screen.getByDisplayValue('Todoist')).toBeInTheDocument());
    expect(screen.getAllByText('web_search').length).toBeGreaterThan(0);
    expect(screen.getByText(/unverified draft/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /find source urls/i }));
    await waitFor(() => expect(screen.getByDisplayValue('https://todoist.com/pricing')).toBeInTheDocument());
    expect(screen.getByLabelText('Track source https://todoist.com/pricing')).toBeInTheDocument();
  });

  it('supports manual entry with criteria and extra sources', () => {
    render(<WatchlistSetup onCreated={() => {}} onCancel={() => {}} />);
    expect(screen.getByLabelText('Name')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /add criterion/i }));
    expect(screen.getAllByLabelText('Criterion field')).toHaveLength(2);
    fireEvent.click(screen.getByRole('button', { name: /add competitor/i }));
    expect(screen.getAllByLabelText('Product name')).toHaveLength(2);
  });

  it('searches source URLs only for picked competitor domains', async () => {
    render(<WatchlistSetup onCreated={() => {}} onCancel={() => {}} />);
    fireEvent.change(screen.getByLabelText(/describe your product/i), { target: { value: 'I make TaskFlow' } });
    fireEvent.click(screen.getByRole('button', { name: /find competitors/i }));
    await waitFor(() => expect(screen.getByDisplayValue('ClickUp')).toBeInTheDocument());
    fireEvent.click(screen.getByLabelText('Track ClickUp'));
    fireEvent.click(screen.getByRole('button', { name: /find source urls/i }));
    await waitFor(() => expect(screen.getByDisplayValue('https://todoist.com/pricing')).toBeInTheDocument());
    expect(screen.queryByDisplayValue('https://clickup.com/pricing')).not.toBeInTheDocument();

    const sourceCall = vi.mocked(fetch).mock.calls.find(([url]) => String(url).includes('/discover-sources'));
    expect(JSON.parse(sourceCall[1].body)).toEqual({
      domains: ['https://todoist.com/'],
      dimension: 'pricing'
    });
  });
});
