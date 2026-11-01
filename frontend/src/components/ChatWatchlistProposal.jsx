import { useState } from 'react';
import { Search, Sparkles } from 'lucide-react';
import { createWatchlist, discoverCompetitors, discoverSources } from '../services/ciApi';
import { Alert, AlertDescription } from './ui/alert';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Card } from './ui/card';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { Spinner } from './ui/spinner';
import ConfirmationBanner from './ConfirmationBanner';
import ErrorNotice from './ErrorNotice';

const PROVENANCE_HINTS = {
  model_knowledge: 'may be stale — verify'
};

function lastUserText(messages) {
  const mine = (messages || []).filter((message) => message.sender === 'user');
  const last = mine.at(-1);
  const text = last?.content ?? last?.text ?? '';
  return typeof text === 'string' ? text : '';
}

// Turns the latest chat message into a reviewable watchlist draft; the chat input
// is the only place the user types, so there is no separate setup form.
export default function ChatWatchlistProposal({ messages, onCreated }) {
  const prompt = lastUserText(messages);
  const [recommendation, setRecommendation] = useState(null);
  const [picks, setPicks] = useState({});
  const [urls, setUrls] = useState({});
  const [urlPicks, setUrlPicks] = useState({});
  const [name, setName] = useState('');
  const [loading, setLoading] = useState(false);
  const [discoveringSources, setDiscoveringSources] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [createdId, setCreatedId] = useState(null);

  if (!prompt) return null;

  function sourceKey(productIndex, sourceIndex) {
    return `${productIndex}:${sourceIndex}`;
  }

  function isPicked(productIndex) {
    if (!(productIndex in picks)) return true;
    return picks[productIndex];
  }

  function isUrlPicked(key) {
    if (!(key in urlPicks)) return true;
    return urlPicks[key];
  }

  function pickedDomains() {
    return (recommendation?.products || [])
      .filter((item, index) => isPicked(index) && item.official_website)
      .map((item) => item.official_website);
  }

  async function handleDiscoverCompetitors() {
    setLoading(true);
    setError('');
    try {
      setRecommendation(await discoverCompetitors(prompt));
      setPicks({});
      setUrls({});
      setUrlPicks({});
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function handleDiscoverSources() {
    const domains = pickedDomains();
    if (domains.length === 0) {
      setError('Pick at least one competitor with an official website first.');
      return;
    }
    setDiscoveringSources(true);
    setError('');
    try {
      const dimension = recommendation.suggested_dimensions?.[0] || 'pricing';
      const sourcesByDomain = await discoverSources(domains, dimension);
      setRecommendation((current) => ({
        ...current,
        products: (current.products || []).map((item, index) => {
          const found = isPicked(index) && item.official_website ? sourcesByDomain[item.official_website] : null;
          return found ? { ...item, sources: found.map((source) => ({ ...source, tracked: true })) } : item;
        })
      }));
    } catch (err) {
      setError(err.message);
    } finally {
      setDiscoveringSources(false);
    }
  }

  async function handleCreate() {
    setSaving(true);
    setError('');
    try {
      const tracked = (recommendation.products || [])
        .map((item, index) => ({ item, index }))
        .filter(({ index }) => isPicked(index));
      const watchlist = await createWatchlist({
        name: name.trim() || `${tracked[0]?.item.name || 'Competitive'} watch`,
        description: '',
        config: {
          goal: recommendation.suggested_goal || prompt,
          dimensions: recommendation.suggested_dimensions?.length ? recommendation.suggested_dimensions : ['pricing'],
          workflow_version_id: null,
          comparison_criteria: (recommendation.suggested_criteria || []).map((item) => ({
            field: item.field,
            objective: item.objective,
            importance: item.importance || 'medium'
          })),
          products: tracked.map(({ item, index }) => ({
            name: item.name,
            kind: item.kind,
            company: item.company || '',
            official_website: item.official_website || null,
            profile: { facts: [], target_segments: [] },
            sources: (item.sources || [])
              .map((source, sub) => ({ source, key: sourceKey(index, sub) }))
              .filter(({ source, key }) => (urls[key] ?? source.url).trim() && isUrlPicked(key))
              .map(({ source, key }) => ({
                url: (urls[key] ?? source.url).trim(),
                kind: source.kind,
                enabled: true
              }))
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
      <Card className="wizard-shell wizard-complete" data-testid="chat-proposal-done">
        <ConfirmationBanner message="Watchlist created from this chat." />
        <div>
          <Button type="button" variant="outline" onClick={() => onCreated && onCreated(createdId)}>
            View in Intelligence
          </Button>
        </div>
      </Card>
    );
  }

  return (
    <Card className="wizard-shell" data-testid="chat-proposal">
      <div className="wizard-panel__heading">
        <span className="wizard-panel__mark" aria-hidden="true"><Sparkles /></span>
        <div>
          <p className="wizard-panel__title">Turn this chat into a watchlist</p>
          <p className="wizard-panel__description">
            AgentFlow searches the web for competitors, then finds same-site source URLs only for the competitors you pick.
            Review everything before creating.
          </p>
        </div>
      </div>
      <div className="wizard-actions">
        <Button type="button" variant="outline" onClick={handleDiscoverCompetitors} disabled={loading || !prompt}>
          {loading
            ? <Spinner data-icon="inline-start" aria-hidden="true" />
            : <Search data-icon="inline-start" />}
          {loading ? 'Searching…' : 'Find competitors'}
        </Button>
        {recommendation && (
          <Button
            type="button"
            variant="outline"
            onClick={handleDiscoverSources}
            disabled={discoveringSources || pickedDomains().length === 0}
          >
            {discoveringSources && <Spinner data-icon="inline-start" aria-hidden="true" />}
            {discoveringSources ? 'Searching…' : 'Find source URLs'}
          </Button>
        )}
      </div>
      {error && (
        <ErrorNotice message={error} onDismiss={() => setError('')} />
      )}
      {recommendation && (
        <div className="flex flex-col gap-3">
          <Alert className="wizard-unverified">
            <AlertDescription>Unverified draft — review every entry before creating.</AlertDescription>
          </Alert>
          {(recommendation.warnings || []).length > 0 && (
            <p className="wizard-field__hint">{recommendation.warnings.join(' ')}</p>
          )}
          <div className="wizard-card-list">
            {recommendation.products.map((item, index) => (
              <Card key={index} size="sm" className="wizard-competitor-card" data-tracked={isPicked(index)}>
                <div className="wizard-competitor-card__head">
                  <label className="wizard-check">
                    <input
                      type="checkbox"
                      checked={isPicked(index)}
                      onChange={(event) => setPicks((current) => ({ ...current, [index]: event.target.checked }))}
                      aria-label={`Track ${item.name}`}
                    />
                    <span className="wizard-competitor-card__title">
                      {item.name}
                      <span className="ci-chip ci-chip--quiet ml-2">{item.kind}</span>
                    </span>
                  </label>
                  {item.provenance && (
                    <Badge
                      variant="outline"
                      title={PROVENANCE_HINTS[item.provenance] || undefined}
                    >
                      {item.provenance}
                    </Badge>
                  )}
                </div>
                {item.rationale && (
                  <p className="wizard-competitor-card__rationale">{item.rationale}</p>
                )}
                {(item.sources || []).length > 0 && (
                  <div className="wizard-source-list">
                    {item.sources.map((source, sub) => {
                      const key = sourceKey(index, sub);
                      return (
                        <div key={sub} className="wizard-source-row">
                          <input
                            type="checkbox"
                            checked={isUrlPicked(key)}
                            onChange={(event) => setUrlPicks((current) => ({ ...current, [key]: event.target.checked }))}
                            aria-label={`Track source ${source.url}`}
                          />
                          <Input
                            value={urls[key] ?? source.url}
                            onChange={(event) => setUrls((current) => ({ ...current, [key]: event.target.value }))}
                            aria-label={`Source URL for ${item.name}`}
                          />
                          <Badge
                            variant="outline"
                            title={PROVENANCE_HINTS[source.provenance] || undefined}
                          >
                            {source.provenance}
                          </Badge>
                        </div>
                      );
                    })}
                  </div>
                )}
              </Card>
            ))}
          </div>
          <div className="wizard-field">
            <Label htmlFor="chat-proposal-name">Watchlist name</Label>
            <Input id="chat-proposal-name" value={name} onChange={(event) => setName(event.target.value)} placeholder="TaskFlow pricing watch" />
          </div>
          <div>
            <Button type="button" onClick={handleCreate} disabled={saving}>
              {saving && <Spinner data-icon="inline-start" aria-hidden="true" />}
              {saving ? 'Creating…' : 'Create watchlist from picks'}
            </Button>
          </div>
        </div>
      )}
    </Card>
  );
}
