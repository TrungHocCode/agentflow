import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import WatchlistSetup from '../WatchlistSetup';

const RECOMMENDATION = {
  products: [
    {
      name: 'TaskFlow',
      kind: 'own',
      company: 'TaskFlow Inc',
      official_website: 'https://taskflow.example/',
      sources: [],
      rationale: 'Declared by the user.'
    },
    {
      name: 'Todoist',
      kind: 'competitor',
      company: 'Doist',
      official_website: 'https://todoist.com/',
      sources: [
        { url: 'https://todoist.com/pricing', kind: 'pricing', provenance: 'model_knowledge', note: '' }
      ],
      rationale: 'Direct competitor.'
    }
  ],
  suggested_goal: 'Track Todoist pricing vs TaskFlow',
  suggested_dimensions: ['pricing'],
  suggested_criteria: [{ field: 'price', objective: 'SMB affordability', importance: 'high' }],
  warnings: ['All entries are unverified drafts.']
};

describe('WatchlistSetup wizard', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => RECOMMENDATION })));
  });

  it('fetches recommendations and marks them unverified', async () => {
    render(<WatchlistSetup onCreated={() => {}} onCancel={() => {}} />);
    fireEvent.change(screen.getByLabelText(/describe your product/i), { target: { value: 'I make TaskFlow' } });
    fireEvent.click(screen.getByRole('button', { name: /get recommendations/i }));
    await waitFor(() => expect(screen.getByDisplayValue('Todoist')).toBeInTheDocument());
    expect(screen.getByDisplayValue('https://todoist.com/pricing')).toBeInTheDocument();
    expect(screen.getByText('model_knowledge')).toBeInTheDocument();
    expect(screen.getByText(/unverified draft/i)).toBeInTheDocument();
  });

  it('supports manual entry with criteria and extra sources', () => {
    render(<WatchlistSetup onCreated={() => {}} onCancel={() => {}} />);
    expect(screen.getByLabelText('Name')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /add criterion/i }));
    expect(screen.getAllByLabelText('Criterion field')).toHaveLength(2);
    fireEvent.click(screen.getByRole('button', { name: /add competitor/i }));
    expect(screen.getAllByLabelText('Product name')).toHaveLength(2);
  });
});
