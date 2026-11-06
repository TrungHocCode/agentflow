import { useState } from 'react';
import {
  ArrowLeft,
  ArrowRight,
  Boxes,
  Check,
  CircleDollarSign,
  FileClock,
  Globe2,
  PlugZap,
  Plus,
  Search,
  Sparkles,
  Trash2
} from 'lucide-react';
import { createWatchlist, discoverCompetitors, discoverSources } from '../services/ciApi';
import { countLabel } from '../lib/labels';
import { Alert, AlertDescription } from './ui/alert';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Card } from './ui/card';
import { Input } from './ui/input';
import { Spinner } from './ui/spinner';
import { Textarea } from './ui/textarea';
import ErrorNotice from './ErrorNotice';

const DIMENSIONS = [
  {
    id: 'pricing',
    label: 'Pricing',
    caption: 'Price tiers, add-ons and discounts',
    icon: CircleDollarSign
  },
  {
    id: 'features',
    label: 'Features',
    caption: 'New capabilities and packaging',
    icon: Boxes
  },
  {
    id: 'integrations',
    label: 'Integrations',
    caption: 'Partners, APIs and app marketplaces',
    icon: PlugZap
  },
  {
    id: 'release_notes',
    label: 'Release notes',
    caption: 'Changelogs and shipped updates',
    icon: FileClock
  }
];
const KINDS = ['pricing', 'features', 'integrations', 'release_notes'];
const STEPS = [
  { id: 1, label: 'Who you are' },
  { id: 2, label: 'Competitors' },
  { id: 3, label: 'Scope' },
  { id: 4, label: 'Review' }
];
const PROVENANCE_HINTS = {
  model_knowledge: 'may be stale — verify'
};

function dimensionLabel(value) {
  return String(value || '').replaceAll('_', ' ');
}

function blankProduct(kind) {
  return {
    name: '',
    kind,
    company: '',
    official_website: '',
    sources: [],
    rationale: '',
    tracked: true,
    scope_kinds: []
  };
}

function blankSource() {
  return { url: '', kind: 'pricing', provenance: 'user_supplied', note: '', tracked: true };
}

function withTrackedSources(item, scopeKinds) {
  return {
    ...item,
    tracked: item.tracked ?? true,
    scope_kinds: item.scope_kinds?.length ? item.scope_kinds : [...scopeKinds],
    sources: (item.sources || []).map((source) => ({ ...source, tracked: source.tracked ?? true }))
  };
}

export default function WatchlistSetup({ onCreated, onCancel }) {
  const [step, setStep] = useState(1);
  const [prompt, setPrompt] = useState('');
  const [discoveringCompetitors, setDiscoveringCompetitors] = useState(false);
  const [discoveringSources, setDiscoveringSources] = useState(false);
  const [discovered, setDiscovered] = useState(false);
  const [name, setName] = useState('');
  const [goal, setGoal] = useState('');
  const [dimensions, setDimensions] = useState(['pricing']);
  const [criteria, setCriteria] = useState([{ field: 'price', objective: 'Track public pricing changes', importance: 'high' }]);
  const [products, setProducts] = useState([]);
  const [warnings, setWarnings] = useState([]);
  const [sourceNotice, setSourceNotice] = useState('');
  const [saving, setSaving] = useState(false);
  const [createdId, setCreatedId] = useState(null);
  const [error, setError] = useState('');

  const pickedProducts = products.filter((item) => item.tracked && item.name.trim());
  const pickedDomains = pickedProducts
    .filter((item) => item.official_website?.trim())
    .map((item) => item.official_website.trim());
  const sourceCount = pickedProducts.reduce(
    (total, item) => total + (item.sources || []).filter(
      (source) => source.url?.trim() && source.tracked !== false && scopedKinds(item).includes(source.kind)
    ).length,
    0
  );

  const canAdvance = step === 1
    ? Boolean(prompt.trim()) && dimensions.length > 0
    : pickedProducts.length > 0;
  const canCreate = Boolean(name.trim()) && pickedProducts.length > 0 && dimensions.length > 0;

  function toggleDimension(dimension) {
    setDimensions((current) =>
      current.includes(dimension) ? current.filter((item) => item !== dimension) : [...current, dimension]
    );
  }

  function scopedKinds(item) {
    return Array.isArray(item.scope_kinds) ? item.scope_kinds : dimensions;
  }

  async function handleDiscoverCompetitors(event) {
    event.preventDefault();
    setDiscoveringCompetitors(true);
    setError('');
    try {
      const recommendation = await discoverCompetitors(prompt);
      const competitors = (recommendation.products || []).map((item) =>
        withTrackedSources(
          { ...item, kind: item.kind || 'competitor', tracked: item.suggested === true },
          dimensions.length ? dimensions : ['pricing']
        )
      );
      setProducts((current) => {
        const own = current.filter((item) => item.kind === 'own');
        // A watchlist holds one own product, so keep the one already chosen.
        return [...own, ...competitors.filter((item) => own.length === 0 || item.kind !== 'own')];
      });
      setWarnings(recommendation.warnings || []);
      setDiscovered(true);
      setGoal((current) => current || recommendation.suggested_goal || prompt);
    } catch (err) {
      setError(err.message);
    } finally {
      setDiscoveringCompetitors(false);
    }
  }

  async function handleDiscoverSources() {
    if (pickedDomains.length === 0) {
      setError('Pick at least one competitor with an official website first.');
      return;
    }
    setDiscoveringSources(true);
    setError('');
    try {
      const dimension = dimensions[0] || 'pricing';
      const requested = new Set(pickedDomains);
      const sourcesByDomain = await discoverSources([...requested], dimension);
      const missing = pickedProducts
        .filter((item) => item.official_website && !sourcesByDomain[item.official_website.trim()]?.length)
        .map((item) => item.name.trim());
      setSourceNotice(missing.length
        ? `No ${dimensionLabel(dimension)} pages were found for ${missing.join(', ')}. Paste the URLs below if you know them.`
        : '');
      setProducts((current) =>
        current.map((item) => {
          const found = item.official_website && requested.has(item.official_website)
            ? sourcesByDomain[item.official_website]
            : null;
          if (!found) return item;
          return { ...item, sources: found.map((source) => ({ ...source, tracked: true })) };
        })
      );
      setDiscovered(true);
    } catch (err) {
      setError(err.message);
    } finally {
      setDiscoveringSources(false);
    }
  }

  function updateProduct(index, patch) {
    setProducts((current) => current.map((item, position) => (position === index ? { ...item, ...patch } : item)));
  }

  function addProduct(kind) {
    setProducts((current) => [
      ...current,
      { ...blankProduct(kind), scope_kinds: [...(dimensions.length ? dimensions : ['pricing'])] }
    ]);
  }

  function removeProduct(index) {
    setProducts((current) => current.filter((_, position) => position !== index));
  }

  function updateSource(productIndex, sourceIndex, patch) {
    setProducts((current) =>
      current.map((item, position) => {
        if (position !== productIndex) return item;
        return {
          ...item,
          sources: item.sources.map((source, sub) => (sub === sourceIndex ? { ...source, ...patch } : source))
        };
      })
    );
  }

  function removeSource(productIndex, sourceIndex) {
    setProducts((current) =>
      current.map((item, position) => {
        if (position !== productIndex) return item;
        return { ...item, sources: item.sources.filter((_, sub) => sub !== sourceIndex) };
      })
    );
  }

  function updateCriterion(index, patch) {
    setCriteria((current) => current.map((item, position) => (position === index ? { ...item, ...patch } : item)));
  }

  function goNext() {
    if (!canAdvance || step >= STEPS.length) return;
    if (step === STEPS.length - 1 && !name.trim()) {
      // Suggest from the products the user reviewed, never from a raw search-result title.
      const anchor = pickedProducts.find((item) => item.kind === 'own') || pickedProducts[0];
      if (anchor) setName(`${anchor.name.trim()} watch`);
    }
    setStep((current) => current + 1);
  }

  function goBack() {
    setStep((current) => Math.max(1, current - 1));
  }

  async function handleSubmit(event) {
    event.preventDefault();
    if (step !== STEPS.length || !canCreate || saving) return;
    setSaving(true);
    setError('');
    try {
      const tracked = products.filter((item) => item.tracked && item.name.trim());
      const watchlist = await createWatchlist({
        name: name.trim(),
        description: '',
        config: {
          goal: goal.trim() || prompt.trim(),
          dimensions,
          workflow_version_id: null,
          comparison_criteria: criteria.filter((item) => item.field.trim() && item.objective.trim()),
          products: tracked.map((item) => ({
            name: item.name.trim(),
            kind: item.kind,
            company: item.company || '',
            official_website: item.official_website || null,
            profile: { facts: [], target_segments: [] },
            sources: (item.sources || [])
              .filter((source) => source.url.trim() && source.tracked !== false && scopedKinds(item).includes(source.kind))
              .map((source) => ({ url: source.url.trim(), kind: source.kind, enabled: true }))
          }))
        }
      });
      setCreatedId(watchlist.id);
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  if (createdId) {
    return (
      <Card className="wizard-shell wizard-complete" data-testid="setup-created">
        <span className="wizard-complete__mark" aria-hidden="true"><Check /></span>
        <p className="page-eyebrow">WATCHLIST READY</p>
        <h2 className="type-display">{name.trim()} is ready</h2>
        <p className="wizard-complete__copy">
          Three short steps turn this draft into a tracked brief. Nothing runs until you approve it.
        </p>
        <ol className="wizard-next-steps">
          <li>
            <span className="wizard-next-steps__index">1</span>
            <span><strong>Check the sources</strong><small>Open the watchlist and correct anything that looks wrong.</small></span>
          </li>
          <li>
            <span className="wizard-next-steps__index">2</span>
            <span><strong>Approve &amp; run</strong><small>Confirm the scope and capture a first baseline for every tracked source.</small></span>
          </li>
          <li>
            <span className="wizard-next-steps__index">3</span>
            <span><strong>Read the brief</strong><small>Most first runs report no change; that is a healthy baseline.</small></span>
          </li>
        </ol>
        <p className="wizard-complete__hint">
          Tip: add a weekly schedule in the watchlist's Settings tab so future check-ins run on their own.
        </p>
        <div className="wizard-nav">
          <Button type="button" onClick={() => onCreated(createdId)}>
            Open watchlist
            <ArrowRight data-icon="inline-end" />
          </Button>
          <Button type="button" variant="ghost" onClick={onCancel}>Back to watchlists</Button>
        </div>
      </Card>
    );
  }

  return (
    <Card className="wizard-shell" data-testid="setup-wizard">
      <form onSubmit={handleSubmit} className="flex flex-col gap-5">
        <header className="wizard-head">
          <div className="wizard-head__copy">
            <p className="page-eyebrow">NEW WATCHLIST</p>
            <h2 className="type-display">Set up competitive tracking</h2>
            <p className="wizard-head__caption">
              A short story: tell us who you are, pick competitors, set the scope, then review before anything runs.
            </p>
          </div>
          <Button type="button" variant="ghost" onClick={onCancel}>Cancel</Button>
        </header>

        <ol className="wizard-stepper" aria-label="Setup steps">
          {STEPS.map((item) => {
            const state = step === item.id ? 'active' : step > item.id ? 'done' : 'upcoming';
            return (
              <li
                key={item.id}
                className="wizard-step"
                data-state={state}
                data-testid={`wizard-step-${item.id}`}
                aria-current={state === 'active' ? 'step' : undefined}
              >
                <span className="wizard-step__index">
                  {state === 'done' ? <Check aria-hidden="true" /> : item.id}
                </span>
                <span className="wizard-step__label">{item.label}</span>
              </li>
            );
          })}
        </ol>

        {error && (
          <ErrorNotice message={error} onDismiss={() => setError('')} />
        )}

        {step === 1 && (
          <section className="wizard-panel" data-testid="setup-step-1">
            <div className="wizard-panel__heading">
              <span className="wizard-panel__mark" aria-hidden="true"><Sparkles /></span>
              <div>
                <h3 className="wizard-panel__title">Who are you, and what should we watch?</h3>
                <p className="wizard-panel__description">
                  One or two sentences is enough. Mention your product and the kind of movement that matters.
                </p>
              </div>
            </div>
            <label className="wizard-field" htmlFor="ci-setup-prompt">
              <span className="wizard-field__label">Your product and tracking intent</span>
              <Textarea
                id="ci-setup-prompt"
                value={prompt}
                onChange={(event) => setPrompt(event.target.value)}
                placeholder="I make TaskFlow, a team task manager. Track pricing of Todoist and ClickUp."
                aria-label="Describe your product and what to track"
                rows={3}
              />
            </label>
            <fieldset className="wizard-fieldset">
              <legend className="wizard-field__label">What dimensions should we compare?</legend>
              <p className="wizard-field__hint">Pick at least one card. You can refine each competitor in step 3.</p>
              <div className="wizard-dimension-grid">
                {DIMENSIONS.map((dimension) => {
                  const Icon = dimension.icon;
                  const selected = dimensions.includes(dimension.id);
                  return (
                    <button
                      key={dimension.id}
                      type="button"
                      className="wizard-dimension-card"
                      data-selected={selected}
                      aria-pressed={selected}
                      onClick={() => toggleDimension(dimension.id)}
                    >
                      <span className="wizard-dimension-card__icon" aria-hidden="true"><Icon /></span>
                      <span className="wizard-dimension-card__copy">
                        <strong>{dimension.label}</strong>
                        <small>{dimension.caption}</small>
                      </span>
                      <span className="wizard-dimension-card__check" aria-hidden="true">
                        {selected ? <Check /> : null}
                      </span>
                    </button>
                  );
                })}
              </div>
            </fieldset>
          </section>
        )}

        {step === 2 && (
          <section className="wizard-panel" data-testid="setup-step-2">
            <div className="wizard-panel__heading">
              <span className="wizard-panel__mark" aria-hidden="true"><Globe2 /></span>
              <div>
                <h3 className="wizard-panel__title">Who should we watch?</h3>
                <p className="wizard-panel__description">
                  Search the web for candidate competitors, or add them manually. Every entry stays editable.
                </p>
              </div>
            </div>
            <div className="wizard-actions">
              <Button
                type="button"
                variant="outline"
                onClick={handleDiscoverCompetitors}
                disabled={discoveringCompetitors || !prompt.trim()}
              >
                {discoveringCompetitors
                  ? <Spinner data-icon="inline-start" aria-hidden="true" />
                  : <Search data-icon="inline-start" />}
                {discoveringCompetitors ? 'Searching…' : 'Find competitors'}
              </Button>
              <Button type="button" variant="outline" onClick={() => addProduct('competitor')}>
                <Plus data-icon="inline-start" />Add competitor
              </Button>
              <Button type="button" variant="ghost" onClick={() => addProduct('own')}>
                <Plus data-icon="inline-start" />Add own product
              </Button>
            </div>

            {(discovered || warnings.length > 0) && (
              <Alert className="wizard-unverified">
                <AlertDescription>
                  Unverified draft — tick what you want to track. Only products you named are ticked for you. {warnings.join(' ')}
                </AlertDescription>
              </Alert>
            )}

            {products.length === 0 ? (
              <div className="wizard-empty empty-state-fade">
                <p>No competitors yet. Search by your description, or add one manually to keep going.</p>
              </div>
            ) : (
              <div className="wizard-card-list">
                {products.map((item, index) => (
                  <Card
                    key={index}
                    size="sm"
                    className="wizard-competitor-card"
                    data-tracked={item.tracked}
                  >
                    <div className="wizard-competitor-card__head">
                      <label className="wizard-check">
                        <input
                          type="checkbox"
                          checked={item.tracked}
                          onChange={(event) => updateProduct(index, { tracked: event.target.checked })}
                          aria-label={`Track ${item.name || 'product'}`}
                        />
                        <span className="wizard-competitor-card__title">
                          {item.name || (item.kind === 'own' ? 'Your product' : 'New competitor')}
                        </span>
                      </label>
                      <div className="wizard-badge-row">
                        {item.provenance && (
                          <Badge
                            variant="outline"
                            title={PROVENANCE_HINTS[item.provenance] || undefined}
                          >
                            {item.provenance}
                          </Badge>
                        )}
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon-xs"
                          onClick={() => removeProduct(index)}
                          aria-label={`Remove ${item.name || 'product'}`}
                          title="Remove"
                        >
                          <Trash2 />
                        </Button>
                      </div>
                    </div>
                    {item.rationale && (
                      <p className="wizard-competitor-card__rationale">{item.rationale}</p>
                    )}
                    <div className="wizard-field-grid">
                      <Input
                        value={item.name}
                        onChange={(event) => updateProduct(index, { name: event.target.value })}
                        placeholder="Product name"
                        aria-label="Product name"
                      />
                      <Input
                        value={item.company}
                        onChange={(event) => updateProduct(index, { company: event.target.value })}
                        placeholder="Company"
                        aria-label="Company"
                      />
                    </div>
                    <Input
                      value={item.official_website || ''}
                      onChange={(event) => updateProduct(index, { official_website: event.target.value })}
                      placeholder="https://product.example/"
                      aria-label="Official website"
                    />
                    <label className="wizard-role">
                      <span>Role</span>
                      <select
                        value={item.kind}
                        onChange={(event) => updateProduct(index, { kind: event.target.value })}
                        aria-label="Product role"
                        className="wizard-select"
                      >
                        <option value="competitor">competitor</option>
                        <option value="own">own</option>
                      </select>
                    </label>
                  </Card>
                ))}
              </div>
            )}
          </section>
        )}

        {step === 3 && (
          <section className="wizard-panel" data-testid="setup-step-3">
            <div className="wizard-panel__heading">
              <span className="wizard-panel__mark" aria-hidden="true"><Boxes /></span>
              <div>
                <h3 className="wizard-panel__title">Set the scope for each competitor</h3>
                <p className="wizard-panel__description">
                  Choose which source kinds matter, then find or paste exact source URLs.
                </p>
              </div>
            </div>
            <div className="wizard-actions">
              <Button
                type="button"
                variant="outline"
                onClick={handleDiscoverSources}
                disabled={discoveringSources || pickedDomains.length === 0}
              >
                {discoveringSources
                  ? <Spinner data-icon="inline-start" aria-hidden="true" />
                  : <Search data-icon="inline-start" />}
                {discoveringSources ? 'Searching…' : 'Find source URLs'}
              </Button>
              <p className="wizard-field__hint">
                Searches only the picked competitor domains for the selected dimension ({dimensions[0] || 'pricing'}).
              </p>
            </div>
            {sourceNotice && <p className="wizard-field__hint" role="status">{sourceNotice}</p>}

            {pickedProducts.length === 0 ? (
              <div className="wizard-empty empty-state-fade">
                <p>Pick at least one competitor in step 2 to continue.</p>
              </div>
            ) : (
              <div className="wizard-card-list">
                {products.map((item, index) => {
                  if (!item.tracked || !item.name.trim()) return null;
                  const activeKinds = scopedKinds(item);
                  return (
                    <Card key={index} size="sm" className="wizard-scope-row">
                      <div className="wizard-scope-row__head">
                        <div className="wizard-scope-row__identity">
                          <strong>{item.name}</strong>
                          <span>{item.official_website || 'No domain yet'}</span>
                        </div>
                        {item.provenance && (
                          <Badge variant="outline" title={PROVENANCE_HINTS[item.provenance] || undefined}>
                            {item.provenance}
                          </Badge>
                        )}
                      </div>
                      <div className="wizard-kind-row">
                        <span className="wizard-field__label">Source kinds</span>
                        <div className="wizard-kind-chips" role="group" aria-label={`Source kinds for ${item.name}`}>
                          {KINDS.map((kind) => {
                            const selected = activeKinds.includes(kind);
                            return (
                              <button
                                key={kind}
                                type="button"
                                className="wizard-kind-chip"
                                data-selected={selected}
                                aria-pressed={selected}
                                onClick={() => {
                                  const next = selected
                                    ? activeKinds.filter((value) => value !== kind)
                                    : [...activeKinds, kind];
                                  updateProduct(index, { scope_kinds: next });
                                }}
                              >
                                {dimensionLabel(kind)}
                              </button>
                            );
                          })}
                        </div>
                      </div>
                      <div className="wizard-source-list">
                        {(item.sources || []).map((source, sub) => (
                          <div key={sub} className="wizard-source-row">
                            <input
                              type="checkbox"
                              checked={source.tracked !== false}
                              onChange={(event) => updateSource(index, sub, { tracked: event.target.checked })}
                              aria-label={`Track source ${source.url || 'row'}`}
                            />
                            <Input
                              value={source.url}
                              onChange={(event) => updateSource(index, sub, { url: event.target.value })}
                              placeholder="https://rival.example/pricing"
                              aria-label="Source URL"
                            />
                            <select
                              value={source.kind}
                              onChange={(event) => updateSource(index, sub, { kind: event.target.value })}
                              aria-label="Source kind"
                              className="wizard-select"
                            >
                              {KINDS.map((kind) => (
                                <option key={kind} value={kind}>{dimensionLabel(kind)}</option>
                              ))}
                            </select>
                            <Badge
                              variant="outline"
                              title={PROVENANCE_HINTS[source.provenance] || undefined}
                            >
                              {source.provenance || 'user_supplied'}
                            </Badge>
                            <Button
                              type="button"
                              variant="ghost"
                              size="icon-xs"
                              onClick={() => removeSource(index, sub)}
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
                            onClick={() => updateProduct(index, { sources: [...(item.sources || []), blankSource()] })}
                          >
                            <Plus data-icon="inline-start" />Add source
                          </Button>
                        </div>
                      </div>
                    </Card>
                  );
                })}
              </div>
            )}

            <div className="wizard-criteria">
              <div className="wizard-criteria__head">
                <div>
                  <p className="wizard-field__label">Comparison criteria</p>
                  <p className="wizard-field__hint">Shared across every tracked competitor.</p>
                </div>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() => setCriteria((current) => [...current, { field: '', objective: '', importance: 'medium' }])}
                >
                  <Plus data-icon="inline-start" />Add criterion
                </Button>
              </div>
              {criteria.map((item, index) => (
                <div key={index} className="wizard-criterion-row">
                  <Input
                    value={item.field}
                    onChange={(event) => updateCriterion(index, { field: event.target.value })}
                    placeholder="price"
                    aria-label="Criterion field"
                  />
                  <Input
                    value={item.objective}
                    onChange={(event) => updateCriterion(index, { objective: event.target.value })}
                    placeholder="SMB affordability"
                    aria-label="Criterion objective"
                  />
                  <select
                    value={item.importance}
                    onChange={(event) => updateCriterion(index, { importance: event.target.value })}
                    aria-label="Criterion importance"
                    className="wizard-select"
                  >
                    <option value="high">high</option>
                    <option value="medium">medium</option>
                    <option value="low">low</option>
                  </select>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-xs"
                    onClick={() => setCriteria((current) => current.filter((_, position) => position !== index))}
                    aria-label="Remove criterion"
                  >
                    <Trash2 />
                  </Button>
                </div>
              ))}
            </div>
          </section>
        )}

        {step === 4 && (
          <section className="wizard-panel" data-testid="setup-step-4">
            <div className="wizard-panel__heading">
              <span className="wizard-panel__mark" aria-hidden="true"><Check /></span>
              <div>
                <h3 className="wizard-panel__title">Review before creating</h3>
                <p className="wizard-panel__description">
                  This is a draft. Creating it does not approve or run anything yet.
                </p>
              </div>
            </div>

            <div className="wizard-review-grid">
              <div className="wizard-review-stat">
                <strong>{pickedProducts.length}</strong>
                <span>products</span>
              </div>
              <div className="wizard-review-stat">
                <strong>{sourceCount}</strong>
                <span>sources</span>
              </div>
              <div className="wizard-review-stat">
                <strong>{dimensions.length}</strong>
                <span>dimensions</span>
              </div>
              <div className="wizard-review-stat">
                <strong>weekly</strong>
                <span>expected cadence</span>
              </div>
            </div>

            <div className="wizard-review-note">
              <p>
                <strong>Expected cadence: weekly.</strong> You can add a schedule after creating; the wizard never runs
                a collection on its own.
              </p>
              <p>
                Honest expectation: many check-ins may report <strong>no change</strong>. That is a healthy result, not a
                failure — a brief only flags movement when a tracked page actually differs.
              </p>
            </div>

            <ul className="wizard-review-list">
              {pickedProducts.map((item, index) => (
                <li key={index}>
                  <span className="wizard-review-list__identity">
                    <strong>{item.name}</strong>
                    <small>{item.official_website || 'No domain yet'}</small>
                  </span>
                  <span className="wizard-review-list__meta">
                    {countLabel((item.sources || []).filter((source) => source.url?.trim()).length, 'source')} ·{' '}
                    {scopedKinds(item).map(dimensionLabel).join(', ')}
                  </span>
                </li>
              ))}
            </ul>

            <label className="wizard-field" htmlFor="ci-setup-name">
              <span className="wizard-field__label">Watchlist name</span>
              <Input
                id="ci-setup-name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="TaskFlow pricing watch"
                aria-label="Watchlist name"
                required
              />
            </label>
            <p className="wizard-review-goal">
              Tracking goal: {goal.trim() || prompt.trim() || 'No description yet.'}
            </p>
          </section>
        )}

        <div className="wizard-nav">
          {step > 1 ? (
            <Button type="button" variant="outline" onClick={goBack}>
              <ArrowLeft data-icon="inline-start" />Back
            </Button>
          ) : (
            <span />
          )}
          {step < STEPS.length ? (
            // Distinct keys keep these as separate DOM nodes; reusing one would turn the clicked
            // Next into type="submit" before the browser runs its default action.
            <Button key="next" type="button" onClick={goNext} disabled={!canAdvance}>
              Next<ArrowRight data-icon="inline-end" />
            </Button>
          ) : (
            <Button key="create" type="submit" disabled={!canCreate || saving}>
              {saving && <Spinner data-icon="inline-start" aria-hidden="true" />}
              {saving ? 'Creating…' : 'Create watchlist'}
            </Button>
          )}
        </div>
      </form>
    </Card>
  );
}
