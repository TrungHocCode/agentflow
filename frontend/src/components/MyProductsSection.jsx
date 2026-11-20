import { useEffect, useState } from 'react';
import {
  createMyProduct, deleteMyProduct, listMyProducts, setDefaultMyProduct, updateMyProduct
} from '../services/ciApi';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Card } from './ui/card';
import { ConfirmDialog } from './ui/confirm-dialog';
import { Field } from './ui/field';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { Textarea } from './ui/textarea';
import ErrorNotice from './ErrorNotice';
import LoadingState from './LoadingState';

const MARKETS = { global: 'Global', vn: 'Vietnam' };
const EMPTY_FORM = { name: '', description: '', description_en: '', website: '', market: 'global', competitors: '' };

function toForm(product) {
  return {
    name: product.name,
    description: product.description || '',
    description_en: product.description_en || '',
    website: product.website || '',
    market: product.market,
    competitors: (product.known_competitors || []).join(', ')
  };
}

function toBody(form) {
  return {
    name: form.name.trim(),
    description: form.description.trim(),
    description_en: form.description_en.trim(),
    website: form.website.trim() || null,
    market: form.market,
    known_competitors: form.competitors.split(',').map((item) => item.trim()).filter(Boolean)
  };
}

export default function MyProductsSection() {
  const [products, setProducts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [form, setForm] = useState(null); // null = closed; { id?, ...fields } = open
  const [saving, setSaving] = useState(false);
  const [pendingDelete, setPendingDelete] = useState(null);

  async function load() {
    setLoading(true);
    setError('');
    try {
      setProducts(await listMyProducts());
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function run(action) {
    setError('');
    try {
      await action();
      setProducts(await listMyProducts());
      return true;
    } catch (err) {
      setError(err.message);
      return false;
    }
  }

  async function handleSubmit(event) {
    event.preventDefault();
    setSaving(true);
    const body = toBody(form);
    const ok = await run(() => (form.id ? updateMyProduct(form.id, body) : createMyProduct(body)));
    setSaving(false);
    if (ok) setForm(null);
  }

  async function handleDelete() {
    const target = pendingDelete;
    setPendingDelete(null);
    await run(() => deleteMyProduct(target.id));
  }

  const setField = (name) => (event) => setForm({ ...form, [name]: event.target.value });

  return (
    <section aria-labelledby="my-products-title" className="flex flex-col gap-3 p-4">
      <div className="flex items-center gap-2">
        <h2 id="my-products-title" className="text-lg font-semibold">My products</h2>
        <span className="flex-1" />
        {!form && <Button type="button" size="sm" onClick={() => setForm({ ...EMPTY_FORM })}>Add product</Button>}
      </div>
      {error && <ErrorNotice message={error} onRetry={load} onDismiss={() => setError('')} />}
      {loading ? (
        <LoadingState label="Loading products" />
      ) : products.length === 0 && !form ? (
        <p className="text-sm text-muted-foreground">
          No products yet. The first product you describe in chat becomes your default, or add one here.
        </p>
      ) : (
        <ul className="flex flex-col gap-2">
          {products.map((product) => (
            <li key={product.id}>
              <Card className="flex flex-col gap-1 p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">{product.name}</span>
                  {product.is_default && <Badge>Default</Badge>}
                  <Badge variant="outline">{MARKETS[product.market] || product.market}</Badge>
                  <span className="flex-1" />
                  {!product.is_default && (
                    <Button
                      type="button" variant="ghost" size="sm"
                      aria-label={`Make ${product.name} default`}
                      onClick={() => run(() => setDefaultMyProduct(product.id))}
                    >
                      Make default
                    </Button>
                  )}
                  <Button
                    type="button" variant="ghost" size="sm"
                    aria-label={`Edit ${product.name}`}
                    onClick={() => setForm({ id: product.id, ...toForm(product) })}
                  >
                    Edit
                  </Button>
                  <Button
                    type="button" variant="ghost" size="sm"
                    aria-label={`Delete ${product.name}`}
                    onClick={() => setPendingDelete(product)}
                  >
                    Delete
                  </Button>
                </div>
                {product.description && <p className="text-sm">{product.description}</p>}
                {product.website && <p className="text-xs text-muted-foreground">{product.website}</p>}
                {product.known_competitors?.length > 0 && (
                  <p className="text-xs text-muted-foreground">
                    Known competitors: {product.known_competitors.join(', ')}
                  </p>
                )}
              </Card>
            </li>
          ))}
        </ul>
      )}
      {form && (
        <form onSubmit={handleSubmit} aria-label={form.id ? 'Edit product' : 'Add product'}
          className="flex flex-col gap-3">
          <Field>
            <Label htmlFor="my-product-name">Name</Label>
            <Input id="my-product-name" required maxLength={120} value={form.name} onChange={setField('name')} />
          </Field>
          <Field>
            <Label htmlFor="my-product-description">Description</Label>
            <Textarea id="my-product-description" maxLength={1000} value={form.description}
              onChange={setField('description')} />
          </Field>
          <Field>
            <Label htmlFor="my-product-description-en">Description in English (used for search)</Label>
            <Textarea id="my-product-description-en" maxLength={1000} value={form.description_en}
              onChange={setField('description_en')} />
          </Field>
          <Field>
            <Label htmlFor="my-product-website">Website</Label>
            <Input id="my-product-website" type="url" value={form.website} onChange={setField('website')} />
          </Field>
          <Field>
            <Label htmlFor="my-product-market">Market</Label>
            <select id="my-product-market" className="ci-select" value={form.market}
              onChange={setField('market')}>
              <option value="global">Global</option>
              <option value="vn">Vietnam</option>
            </select>
          </Field>
          <Field>
            <Label htmlFor="my-product-competitors">Known competitors (comma separated)</Label>
            <Input id="my-product-competitors" value={form.competitors} onChange={setField('competitors')} />
          </Field>
          <div className="flex gap-2">
            <Button type="submit" disabled={saving || !form.name.trim()}>
              {form.id ? 'Save changes' : 'Add product'}
            </Button>
            <Button type="button" variant="ghost" onClick={() => setForm(null)}>Cancel</Button>
          </div>
        </form>
      )}
      <ConfirmDialog
        open={Boolean(pendingDelete)}
        title="Delete this product?"
        description={`"${pendingDelete?.name}" is removed from your account. If it is your default, the oldest remaining product becomes the default.`}
        confirmLabel="Delete"
        tone="destructive"
        onConfirm={handleDelete}
        onCancel={() => setPendingDelete(null)}
      />
    </section>
  );
}
