import { useState } from 'react';
import { Play, Plus, Search, Sparkles } from 'lucide-react';
import { approveAndRun, createWatchlist, discoverCompetitors, discoverSources } from '../services/ciApi';
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
  model_knowledge: 'may be stale — verify',
  site_crawl: "found on the product's own site"
};

function siteHost(url) {
  try {
    return new window.URL(url).hostname.replace(/^www\./, '');
  } catch {
    return '';
  }
}

// Loose mirror of the server's same-site rule; the server still decides at approval.
function onSite(url, website) {
  const host = siteHost(url);
  const site = siteHost(website || '');
  return Boolean(host && site) && (host === site || host.endsWith(`.${site}`) || site.endsWith(`.${host}`));
}

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
  const [sourceNotice, setSourceNotice] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [createdId, setCreatedId] = useState(null);
  const [ownName, setOwnName] = useState('');
  const [running, setRunning] = useState(false);
  const [started, setStarted] = useState(false);

  if (!prompt) return null;

  function sourceKey(productIndex, sourceIndex) {
    return `${productIndex}:${sourceIndex}`;
  }

  function isPicked(productIndex) {
    if (!(productIndex in picks)) return recommendation?.products?.[productIndex]?.suggested === true;
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

  function dimension() {
    return recommendation?.suggested_dimensions?.[0] || 'pricing';
  }

  function updateProducts(change) {
    setRecommendation((current) => ({ ...current, products: change(current.products || []) }));
  }

  function addSource(productIndex) {
    updateProducts((products) => products.map((item, index) => index === productIndex
      ? { ...item, sources: [...(item.sources || []), { url: '', kind: dimension(), provenance: 'manual' }] }
      : item));
  }

  function addOwnProduct() {
    if (!ownName.trim()) return;
    updateProducts((products) => [...products, {
      name: ownName.trim(), kind: 'own', company: '', official_website: null, sources: [], suggested: true
    }]);
    setOwnName('');
  }

  function pickedSourceUrls(productIndex) {
    return (recommendation.products[productIndex].sources || [])
      .map((source, sub) => ({ url: (urls[sourceKey(productIndex, sub)] ?? source.url).trim(), sub }))
      .filter(({ url, sub }) => url && isUrlPicked(sourceKey(productIndex, sub)))
      .map(({ url }) => url);
  }

  // Mirrors the server's approval rules so the user fixes gaps here, not after creating.
  function missingItems() {
    const picked = (recommendation?.products || []).map((item, index) => ({ item, index }))
      .filter(({ index }) => isPicked(index));
    const competitors = picked.filter(({ item }) => item.kind === 'competitor');
    const offSite = picked.flatMap(({ item, index }) => pickedSourceUrls(index)
      .filter((url) => !onSite(url, item.official_website))
      .map((url) => `${url} to be on ${item.name}'s site (${siteHost(item.official_website || '') || 'no website'})`));
    return [
      !picked.some(({ item }) => item.kind === 'own') && 'your own product',
      competitors.length === 0 && 'at least one competitor',
      competitors.length > 0 && !competitors.some(({ index }) => pickedSourceUrls(index).length > 0)
        && 'a source URL for a competitor',
      ...offSite
    ].filter(Boolean);
  }

  async function handleApproveAndRun() {
    setRunning(true);
    setError('');
    try {
      await approveAndRun(createdId);
      setStarted(true);
    } catch (err) {
      setError(err.message);
    } finally {
      setRunning(false);
    }
  }

  async function handleDiscoverCompetitors() {
    setLoading(true);
    setError('');
    let found;
    try {
      found = await discoverCompetitors(prompt);
      setRecommendation(found);
      setPicks({});
      setUrls({});
      setUrlPicks({});
      setSourceNotice('');
    } catch (err) {
      setError(err.message);
      return;
    } finally {
      setLoading(false);
    }
    // Go straight on to the source pages of the pre-ticked products, so the user only reviews.
    const domains = (found.products || [])
      .filter((item) => item.suggested && item.official_website)
      .map((item) => item.official_website);
    if (domains.length) await findSources(domains, found.suggested_dimensions?.[0] || 'pricing');
  }

  async function findSources(domains, kind) {
    setDiscoveringSources(true);
    setError('');
    try {
      const sourcesByDomain = await discoverSources(domains, kind);
      setSourceNotice(domains.some((domain) => !sourcesByDomain[domain]?.length)
        ? 'No source page was found for some products. Use Add source URL for them.'
        : '');
      setRecommendation((current) => ({
        ...current,
        products: (current.products || []).map((item) => {
          const pages = domains.includes(item.official_website) ? sourcesByDomain[item.official_website] : null;
          // Keep anything the user typed when nothing new was found.
          return pages?.length ? { ...item, sources: pages.map((source) => ({ ...source, tracked: true })) } : item;
        })
      }));
    } catch (err) {
      setError(err.message);
    } finally {
      setDiscoveringSources(false);
    }
  }

  async function handleDiscoverSources() {
    const domains = pickedDomains();
    if (domains.length === 0) {
      setError('Pick at least one competitor with an official website first.');
      return;
    }
    await findSources(domains, dimension());
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
          // Approval requires criteria; fall back to one per tracked dimension.
          comparison_criteria: (recommendation.suggested_criteria?.length
            ? recommendation.suggested_criteria
            : (recommendation.suggested_dimensions?.length ? recommendation.suggested_dimensions : ['pricing'])
              .map((field) => ({ field, objective: `Track ${field} changes` }))
          ).map((item) => ({
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
        <ConfirmationBanner message={started
          ? 'First run started. It captures a baseline; changes appear from the next run.'
          : 'Watchlist created. Approve it to capture the first baseline.'} />
        {error && <ErrorNotice message={error} onDismiss={() => setError('')} />}
        <div className="wizard-actions">
          {!started && (
            <Button type="button" onClick={handleApproveAndRun} disabled={running}>
              {running ? <Spinner data-icon="inline-start" aria-hidden="true" /> : <Play data-icon="inline-start" />}
              {running ? 'Starting…' : 'Approve & run'}
            </Button>
          )}
          <Button type="button" variant="outline" onClick={() => onCreated && onCreated(createdId)}>
            Open watchlist
          </Button>
        </div>
      </Card>
    );
  }

  const missing = recommendation ? missingItems() : [];

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
          {sourceNotice && <p className="wizard-field__hint">{sourceNotice}</p>}
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
                      {item.official_website && (
                        <span className="ci-chip ci-chip--quiet ml-2">{siteHost(item.official_website)}</span>
                      )}
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
                            placeholder={`https://…/${dimension()}`}
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
                {isPicked(index) && item.kind === 'competitor' && (
                  <div>
                    <Button type="button" variant="ghost" size="sm" onClick={() => addSource(index)}>
                      <Plus data-icon="inline-start" />Add source URL
                    </Button>
                  </div>
                )}
              </Card>
            ))}
          </div>
          {!recommendation.products.some((item, index) => item.kind === 'own' && isPicked(index)) && (
            <div className="wizard-field">
              <Label htmlFor="chat-proposal-own">Your product</Label>
              <div className="wizard-source-row">
                <Input
                  id="chat-proposal-own"
                  value={ownName}
                  onChange={(event) => setOwnName(event.target.value)}
                  placeholder="TaskFlow"
                />
                <Button type="button" variant="outline" onClick={addOwnProduct} disabled={!ownName.trim()}>
                  Add
                </Button>
              </div>
            </div>
          )}
          <div className="wizard-field">
            <Label htmlFor="chat-proposal-name">Watchlist name</Label>
            <Input id="chat-proposal-name" value={name} onChange={(event) => setName(event.target.value)} placeholder="TaskFlow pricing watch" />
          </div>
          {missing.length > 0 && (
            <p className="wizard-field__hint" role="status">Still needed: {missing.join(', ')}.</p>
          )}
          <div>
            <Button type="button" onClick={handleCreate} disabled={saving || missing.length > 0}>
              {saving && <Spinner data-icon="inline-start" aria-hidden="true" />}
              {saving ? 'Creating…' : 'Create watchlist from picks'}
            </Button>
          </div>
        </div>
      )}
    </Card>
  );
}
