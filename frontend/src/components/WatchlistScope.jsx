import { useState } from 'react';
import { Pencil, Plus, Trash2 } from 'lucide-react';
import { updateWatchlist } from '../services/ciApi';
import { Button } from './ui/button';
import { Card } from './ui/card';
import { Input } from './ui/input';
import { Spinner } from './ui/spinner';
import ErrorNotice from './ErrorNotice';
import { countLabel } from '../lib/labels';

function kindLabel(value) {
  return String(value || '').replaceAll('_', ' ');
}

function toPayloadProducts(draftProducts, originalProducts) {
  const originalUrls = new Map(
    originalProducts.flatMap((product) => (product.sources || []).map((source) => [source.id, source.url]))
  );
  return draftProducts.map((product) => ({
    ...product,
    name: product.name.trim(),
    official_website: product.official_website?.trim() || null,
    sources: (product.sources || [])
      .map((source) => ({ ...source, url: source.url.trim() }))
      .filter((source) => source.url)
      .map((source) => {
        // A source's identity anchors its baseline. Pointing it at another page must start a
        // new identity, otherwise the next run would report the new page as a change.
        if (!source.id || originalUrls.get(source.id) === source.url) return source;
        const fresh = { ...source };
        delete fresh.id;
        return fresh;
      })
  }));
}

export default function WatchlistScope({ watchlist, onSaved }) {
  const revision = watchlist.current_revision;
  const products = revision?.config?.products || [];
  // The backend accepts only source kinds within the watchlist's dimensions.
  const kinds = revision?.config?.dimensions || [];
  const [draft, setDraft] = useState(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  function startEditing() {
    setError('');
    setDraft({ name: watchlist.name, products: JSON.parse(JSON.stringify(products)) });
  }

  function updateProduct(index, patch) {
    setDraft((current) => ({
      ...current,
      products: current.products.map((item, position) => (position === index ? { ...item, ...patch } : item))
    }));
  }

  function updateSource(index, sub, patch) {
    updateProduct(index, {
      sources: draft.products[index].sources.map((item, position) => (position === sub ? { ...item, ...patch } : item))
    });
  }

  function addProduct(kind) {
    setDraft((current) => ({
      ...current,
      products: [...current.products, { name: '', kind, company: '', official_website: '', sources: [] }]
    }));
  }

  const canSave = draft
    && draft.name.trim()
    && draft.products.length > 0
    && draft.products.every((item) => item.name.trim());

  async function save() {
    if (!canSave || saving) return;
    setSaving(true);
    setError('');
    try {
      const updated = await updateWatchlist(watchlist.id, {
        name: draft.name.trim(),
        description: watchlist.description || '',
        expected_revision_id: revision.id,
        config: { ...revision.config, products: toPayloadProducts(draft.products, products) }
      });
      setDraft(null);
      onSaved(updated);
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  if (!draft) {
    return (
      <Card className="ci-section p-4" data-testid="watchlist-scope">
        <div className="ci-section-head">
          <div>
            <p className="ci-section-title">Products and sources</p>
            <p className="ci-section-caption">Only these exact pages are collected.</p>
          </div>
          <Button type="button" variant="outline" onClick={startEditing}>
            <Pencil data-icon="inline-start" />Edit
          </Button>
        </div>
        <ul className="ci-list">
          {products.map((product) => (
            <li key={product.id || product.name} className="ci-row-card flex flex-col gap-2">
              <div className="ci-chip-row">
                <span className="ci-row-title">{product.name}</span>
                <span className="ci-chip ci-chip--quiet">{product.kind === 'own' ? 'your product' : 'competitor'}</span>
                <span className="ci-chip ci-chip--quiet">{countLabel((product.sources || []).length, 'source')}</span>
              </div>
              {(product.sources || []).length === 0 ? (
                <p className="ci-row-meta">No sources yet, so this product is not collected.</p>
              ) : (
                <ul className="flex flex-col gap-1">
                  {product.sources.map((source) => (
                    <li key={source.id || source.url} className="ci-row-meta flex flex-wrap items-center gap-2">
                      <span className="break-all">{source.url}</span>
                      <span className="ci-chip ci-chip--quiet">{kindLabel(source.kind)}</span>
                      {source.enabled === false && <span className="ci-chip ci-chip--quiet">disabled</span>}
                    </li>
                  ))}
                </ul>
              )}
            </li>
          ))}
        </ul>
      </Card>
    );
  }

  return (
    <Card className="ci-section p-4" data-testid="watchlist-scope-edit">
      <div>
        <p className="ci-section-title">Edit products and sources</p>
        <p className="ci-section-caption">
          Saving creates a new revision that needs approval. Earlier revisions and runs are kept.
        </p>
      </div>
      {error && <ErrorNotice message={error} onDismiss={() => setError('')} />}
      <label className="wizard-field">
        <span className="wizard-field__label">Watchlist name</span>
        <Input
          value={draft.name}
          onChange={(event) => setDraft((current) => ({ ...current, name: event.target.value }))}
          aria-label="Watchlist name"
        />
      </label>
      <ul className="ci-list">
        {draft.products.map((product, index) => (
          <li key={product.id || `new-${index}`} className="ci-row-card flex flex-col gap-2">
            <div className="wizard-field-grid">
              <Input
                value={product.name}
                onChange={(event) => updateProduct(index, { name: event.target.value })}
                placeholder="Product name"
                aria-label="Product name"
              />
              <Input
                value={product.official_website || ''}
                onChange={(event) => updateProduct(index, { official_website: event.target.value })}
                placeholder="https://product.example/"
                aria-label={`Official website of ${product.name || 'product'}`}
              />
            </div>
            <div className="ci-chip-row">
              <span className="ci-chip ci-chip--quiet">{product.kind === 'own' ? 'your product' : 'competitor'}</span>
              <span className="flex-1" />
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => setDraft((current) => ({
                  ...current,
                  products: current.products.filter((_, position) => position !== index)
                }))}
              >
                <Trash2 data-icon="inline-start" />Remove product
              </Button>
            </div>
            {(product.sources || []).map((source, sub) => (
              <div key={source.id || `new-${sub}`} className="flex flex-wrap items-center gap-2">
                <Input
                  className="min-w-[14rem] flex-1"
                  value={source.url}
                  onChange={(event) => updateSource(index, sub, { url: event.target.value })}
                  placeholder="https://product.example/pricing"
                  aria-label="Source URL"
                />
                <select
                  className="wizard-select"
                  value={source.kind}
                  onChange={(event) => updateSource(index, sub, { kind: event.target.value })}
                  aria-label="Source kind"
                >
                  {kinds.map((kind) => <option key={kind} value={kind}>{kindLabel(kind)}</option>)}
                </select>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-xs"
                  onClick={() => updateProduct(index, {
                    sources: product.sources.filter((_, position) => position !== sub)
                  })}
                  aria-label="Remove source"
                >
                  <Trash2 />
                </Button>
              </div>
            ))}
            <div>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => updateProduct(index, {
                  sources: [...(product.sources || []), { url: '', kind: kinds[0], enabled: true }]
                })}
              >
                <Plus data-icon="inline-start" />Add source
              </Button>
            </div>
          </li>
        ))}
      </ul>
      <div className="ci-heading-actions">
        <Button type="button" variant="outline" onClick={() => addProduct('competitor')}>
          <Plus data-icon="inline-start" />Add competitor
        </Button>
        {!draft.products.some((item) => item.kind === 'own') && (
          <Button type="button" variant="ghost" onClick={() => addProduct('own')}>
            <Plus data-icon="inline-start" />Add your product
          </Button>
        )}
        <span className="flex-1" />
        <Button type="button" variant="ghost" onClick={() => setDraft(null)} disabled={saving}>Cancel</Button>
        <Button type="button" onClick={save} disabled={!canSave || saving}>
          {saving && <Spinner data-icon="inline-start" aria-hidden="true" />}
          {saving ? 'Saving…' : 'Save changes'}
        </Button>
      </div>
    </Card>
  );
}
