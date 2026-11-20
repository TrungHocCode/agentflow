import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import MyProductsSection from '../MyProductsSection';

const CAFE = {
  id: 'p1', name: 'CafePOS', description: 'POS for cafes', description_en: '', website: 'https://cafepos.example.com',
  market: 'vn', known_competitors: ['Square'], is_default: true
};
const NOTES = { ...CAFE, id: 'p2', name: 'Notes', description: '', website: null, market: 'global',
  known_competitors: [], is_default: false };

function stubApi(initial) {
  let items = initial;
  const calls = [];
  vi.stubGlobal('fetch', vi.fn(async (url, options = {}) => {
    const method = options.method || 'GET';
    calls.push({ url, method, body: options.body ? JSON.parse(options.body) : null });
    if (method === 'POST' && url.endsWith('/my-products')) {
      items = [...items, { ...NOTES, id: 'p3', ...JSON.parse(options.body) }];
    }
    if (method === 'POST' && url.endsWith('/default')) {
      const id = url.split('/').at(-2);
      items = items.map((p) => ({ ...p, is_default: p.id === id }));
    }
    if (method === 'DELETE') items = items.filter((p) => !url.endsWith(`/${p.id}`));
    if (method === 'DELETE') return { ok: true, status: 204 };
    return { ok: true, status: 200, json: async () => (method === 'GET' ? items : items.at(-1)) };
  }));
  return calls;
}

describe('MyProductsSection', () => {
  beforeEach(() => vi.unstubAllGlobals());

  it('lists products with the default badge, market and competitors', async () => {
    stubApi([CAFE, NOTES]);
    render(<MyProductsSection />);
    expect(await screen.findByText('CafePOS')).toBeInTheDocument();
    expect(screen.getAllByText('Default')).toHaveLength(1);
    expect(screen.getByText('Vietnam')).toBeInTheDocument();
    expect(screen.getByText(/Known competitors: Square/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Make CafePOS default' })).not.toBeInTheDocument();
  });

  it('explains the empty state', async () => {
    stubApi([]);
    render(<MyProductsSection />);
    expect(await screen.findByText(/first product you describe in chat becomes your default/i)).toBeInTheDocument();
  });

  it('creates a product', async () => {
    const calls = stubApi([CAFE]);
    render(<MyProductsSection />);
    await screen.findByText('CafePOS');
    fireEvent.click(screen.getByRole('button', { name: 'Add product' }));
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Notes' } });
    fireEvent.change(screen.getByLabelText('Market'), { target: { value: 'vn' } });
    fireEvent.change(screen.getByLabelText(/Known competitors/), { target: { value: 'Obsidian, Notion' } });
    fireEvent.click(within(screen.getByRole('form', { name: 'Add product' })).getByRole('button', { name: 'Add product' }));
    await waitFor(() => expect(screen.getByText('Notes')).toBeInTheDocument());
    const post = calls.find((c) => c.method === 'POST');
    expect(post.body).toMatchObject({ name: 'Notes', market: 'vn', known_competitors: ['Obsidian', 'Notion'], website: null });
  });

  it('makes a product default', async () => {
    const calls = stubApi([CAFE, NOTES]);
    render(<MyProductsSection />);
    fireEvent.click(await screen.findByRole('button', { name: 'Make Notes default' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Make CafePOS default' })).toBeInTheDocument());
    expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/my-products/p2/default'))).toBe(true);
  });

  it('deletes only after confirmation', async () => {
    const calls = stubApi([CAFE, NOTES]);
    render(<MyProductsSection />);
    fireEvent.click(await screen.findByRole('button', { name: 'Delete Notes' }));
    expect(calls.some((c) => c.method === 'DELETE')).toBe(false);
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(screen.queryByText('Notes')).not.toBeInTheDocument());
    expect(calls.some((c) => c.method === 'DELETE' && c.url.endsWith('/my-products/p2'))).toBe(true);
  });
});
