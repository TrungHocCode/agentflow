import { useState } from 'react';
import { createWatchlist, recommendWatchlists } from '../services/ciApi';
import { Alert, AlertDescription } from './ui/alert';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Card } from './ui/card';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { Textarea } from './ui/textarea';

const DIMENSIONS = ['pricing', 'features', 'integrations', 'release_notes'];
const KINDS = ['pricing', 'features', 'integrations', 'release_notes'];

function blankProduct(kind) {
  return {
    name: '',
    kind,
    company: '',
    official_website: '',
    sources: [],
    rationale: '',
    tracked: true
  };
}

function blankSource() {
  return { url: '', kind: 'pricing', provenance: 'user_supplied', note: '' };
}

export default function WatchlistSetup({ onCreated, onCancel }) {
  const [prompt, setPrompt] = useState('');
  const [recommending, setRecommending] = useState(false);
  const [name, setName] = useState('');
  const [goal, setGoal] = useState('');
  const [dimensions, setDimensions] = useState(['pricing']);
  const [criteria, setCriteria] = useState([{ field: 'price', objective: 'Track public pricing changes', importance: 'high' }]);
  const [products, setProducts] = useState([{ ...blankProduct('own'), name: '', tracked: true }]);
  const [warnings, setWarnings] = useState([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  function toggleDimension(dimension) {
    setDimensions((current) =>
      current.includes(dimension) ? current.filter((item) => item !== dimension) : [...current, dimension]
    );
  }

  async function handleRecommend(event) {
    event.preventDefault();
    setRecommending(true);
    setError('');
    try {
      const recommendation = await recommendWatchlists(prompt);
      if (recommendation.suggested_goal) setGoal(recommendation.suggested_goal);
      if (recommendation.suggested_dimensions?.length) setDimensions(recommendation.suggested_dimensions);
      if (recommendation.suggested_criteria?.length) {
        setCriteria(recommendation.suggested_criteria.map((item) => ({ ...item })));
      }
      if (recommendation.products?.length) {
        setProducts(recommendation.products.map((item) => ({ ...item, tracked: true })));
      }
      setWarnings(recommendation.warnings || []);
    } catch (err) {
      setError(err.message);
    } finally {
      setRecommending(false);
    }
  }

  function updateProduct(index, patch) {
    setProducts((current) => current.map((item, position) => (position === index ? { ...item, ...patch } : item)));
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

  async function handleSubmit(event) {
    event.preventDefault();
    setSaving(true);
    setError('');
    try {
      const tracked = products.filter((item) => item.tracked && item.name.trim());
      const watchlist = await createWatchlist({
        name,
        description: '',
        config: {
          goal,
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
              .filter((source) => source.url.trim())
              .map((source) => ({ url: source.url.trim(), kind: source.kind, enabled: true }))
          }))
        }
      });
      onCreated(watchlist.id);
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card className="p-4">
      <form onSubmit={handleSubmit} className="flex flex-col gap-4">
        <h2 className="text-lg font-semibold">New watchlist</h2>
        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
        <div className="flex flex-col gap-1">
          <Label htmlFor="ci-setup-prompt">Describe your product and what to track (optional)</Label>
          <Textarea
            id="ci-setup-prompt"
            value={prompt}
            onChange={(event) => setPrompt(event.target.value)}
            placeholder="I make TaskFlow, a team task manager. Track pricing of Todoist and ClickUp."
          />
          <div>
            <Button type="button" variant="outline" onClick={handleRecommend} disabled={recommending || !prompt.trim()}>
              {recommending ? 'Recommending…' : 'Get recommendations'}
            </Button>
          </div>
        </div>
        {warnings.length > 0 && (
          <Alert>
            <AlertDescription>
              Unverified draft — review every entry before creating. {warnings.join(' ')}
            </AlertDescription>
          </Alert>
        )}
        <div className="flex flex-col gap-1">
          <Label htmlFor="ci-setup-name">Name</Label>
          <Input id="ci-setup-name" value={name} onChange={(event) => setName(event.target.value)} required />
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor="ci-setup-goal">Goal</Label>
          <Textarea id="ci-setup-goal" value={goal} onChange={(event) => setGoal(event.target.value)} required />
        </div>
        <fieldset className="flex flex-col gap-1">
          <legend className="text-sm font-medium">Dimensions</legend>
          <div className="flex flex-wrap gap-2">
            {DIMENSIONS.map((dimension) => (
              <label key={dimension} className="flex items-center gap-1 text-sm">
                <input type="checkbox" checked={dimensions.includes(dimension)} onChange={() => toggleDimension(dimension)} />
                {dimension.replaceAll('_', ' ')}
              </label>
            ))}
          </div>
        </fieldset>
        <div className="flex flex-col gap-2">
          <p className="text-sm font-medium">Comparison criteria</p>
          {criteria.map((item, index) => (
            <div key={index} className="grid grid-cols-[1fr_2fr_auto_auto] items-center gap-2">
              <Input value={item.field} onChange={(event) => updateCriterion(index, { field: event.target.value })} placeholder="price" aria-label="Criterion field" />
              <Input value={item.objective} onChange={(event) => updateCriterion(index, { objective: event.target.value })} placeholder="SMB affordability" aria-label="Criterion objective" />
              <select value={item.importance} onChange={(event) => updateCriterion(index, { importance: event.target.value })} aria-label="Criterion importance" className="rounded border p-1 text-sm">
                <option value="high">high</option>
                <option value="medium">medium</option>
                <option value="low">low</option>
              </select>
              <Button type="button" variant="ghost" onClick={() => setCriteria((current) => current.filter((_, position) => position !== index))} aria-label="Remove criterion">
                ×
              </Button>
            </div>
          ))}
          <div>
            <Button type="button" variant="ghost" onClick={() => setCriteria((current) => [...current, { field: '', objective: '', importance: 'medium' }])}>
              Add criterion
            </Button>
          </div>
        </div>
        <div className="flex flex-col gap-3">
          <p className="text-sm font-medium">Products</p>
          {products.map((item, index) => (
            <Card key={index} className="flex flex-col gap-2 p-3">
              <label className="flex items-center gap-2 text-sm font-medium">
                <input type="checkbox" checked={item.tracked} onChange={(event) => updateProduct(index, { tracked: event.target.checked })} />
                {item.kind === 'own' ? 'Own product' : 'Competitor'}
                {item.rationale && <Badge variant="outline">{item.provenance || 'draft'}</Badge>}
              </label>
              <div className="grid grid-cols-2 gap-2">
                <Input value={item.name} onChange={(event) => updateProduct(index, { name: event.target.value })} placeholder="Product name" aria-label="Product name" />
                <Input value={item.company} onChange={(event) => updateProduct(index, { company: event.target.value })} placeholder="Company" aria-label="Company" />
              </div>
              <Input value={item.official_website || ''} onChange={(event) => updateProduct(index, { official_website: event.target.value })} placeholder="https://product.example/" aria-label="Official website" />
              <div className="flex flex-col gap-1">
                {(item.sources || []).map((source, sub) => (
                  <div key={sub} className="grid grid-cols-[2fr_1fr_auto_auto] items-center gap-2">
                    <Input value={source.url} onChange={(event) => updateSource(index, sub, { url: event.target.value })} placeholder="https://rival.example/pricing" aria-label="Source URL" />
                    <select value={source.kind} onChange={(event) => updateSource(index, sub, { kind: event.target.value })} aria-label="Source kind" className="rounded border p-1 text-sm">
                      {KINDS.map((kind) => (
                        <option key={kind} value={kind}>{kind}</option>
                      ))}
                    </select>
                    <Badge variant="outline">{source.provenance || 'user_supplied'}</Badge>
                    <Button type="button" variant="ghost" onClick={() => removeSource(index, sub)} aria-label="Remove source">
                      ×
                    </Button>
                  </div>
                ))}
                <div>
                  <Button type="button" variant="ghost" onClick={() => updateProduct(index, { sources: [...(item.sources || []), blankSource()] })}>
                    Add source
                  </Button>
                </div>
              </div>
              {item.kind === 'own' ? null : (
                <label className="flex items-center gap-1 text-sm">
                  Role
                  <select value={item.kind} onChange={(event) => updateProduct(index, { kind: event.target.value })} aria-label="Product role" className="rounded border p-1 text-sm">
                    <option value="competitor">competitor</option>
                    <option value="own">own</option>
                  </select>
                </label>
              )}
            </Card>
          ))}
          <div className="flex gap-2">
            <Button type="button" variant="ghost" onClick={() => setProducts((current) => [...current, { ...blankProduct('competitor') }])}>
              Add competitor
            </Button>
            <Button type="button" variant="ghost" onClick={() => setProducts((current) => [...current, { ...blankProduct('own') }])}>
              Add own product
            </Button>
          </div>
        </div>
        <div className="flex gap-2">
          <Button type="submit" disabled={saving || dimensions.length === 0}>
            {saving ? 'Saving…' : 'Create watchlist'}
          </Button>
          <Button type="button" variant="ghost" onClick={onCancel}>
            Cancel
          </Button>
        </div>
      </form>
    </Card>
  );
}
