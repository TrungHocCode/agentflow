import { useState } from 'react';
import { createWatchlist, recommendWatchlists } from '../services/ciApi';
import { Alert, AlertDescription } from './ui/alert';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Card } from './ui/card';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { Textarea } from './ui/textarea';

function lastUserText(messages) {
  const mine = (messages || []).filter((message) => message.sender === 'user');
  const last = mine.at(-1);
  const text = last?.content ?? last?.text ?? '';
  return typeof text === 'string' ? text : '';
}

export default function ChatWatchlistProposal({ messages, onCreated }) {
  const [prompt, setPrompt] = useState(() => lastUserText(messages));
  const [recommendation, setRecommendation] = useState(null);
  const [picks, setPicks] = useState({});
  const [urls, setUrls] = useState({});
  const [name, setName] = useState('');
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [createdId, setCreatedId] = useState(null);

  function sourceKey(productIndex, sourceIndex) {
    return `${productIndex}:${sourceIndex}`;
  }

  function isPicked(productIndex) {
    if (!(productIndex in picks)) return true;
    return picks[productIndex];
  }

  async function handleSuggest(event) {
    event.preventDefault();
    setLoading(true);
    setError('');
    try {
      setRecommendation(await recommendWatchlists(prompt));
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function handleCreate() {
    setSaving(true);
    setError('');
    try {
      const tracked = (recommendation.products || []).filter((_, index) => isPicked(index));
      const watchlist = await createWatchlist({
        name: name.trim() || `${tracked[0]?.name || 'Competitive'} watch`,
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
          products: tracked.map((item, index) => ({
            name: item.name,
            kind: item.kind,
            company: item.company || '',
            official_website: item.official_website || null,
            profile: { facts: [], target_segments: [] },
            sources: (item.sources || []).map((source, sub) => ({
              url: (urls[sourceKey(index, sub)] ?? source.url).trim(),
              kind: source.kind,
              enabled: true
            })).filter((source) => source.url)
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
      <Card className="p-3" data-testid="chat-proposal-done">
        <p className="text-sm font-medium">Watchlist created from this chat.</p>
        <Button type="button" variant="outline" onClick={() => onCreated && onCreated(createdId)}>
          View in Intelligence
        </Button>
      </Card>
    );
  }

  return (
    <Card className="p-3" data-testid="chat-proposal">
      <form onSubmit={handleSuggest} className="flex flex-col gap-2">
        <Label htmlFor="chat-proposal-prompt">Describe your product and rivals to track</Label>
        <Textarea
          id="chat-proposal-prompt"
          value={prompt}
          onChange={(event) => setPrompt(event.target.value)}
          placeholder="I make TaskFlow. Track pricing of Todoist and ClickUp."
        />
        <div>
          <Button type="submit" variant="outline" disabled={loading || !prompt.trim()}>
            {loading ? 'Suggesting…' : 'Suggest watchlist'}
          </Button>
        </div>
      </form>
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      {recommendation && (
        <div className="mt-2 flex flex-col gap-2">
          <Alert>
            <AlertDescription>Unverified draft — review every entry before creating.</AlertDescription>
          </Alert>
          {(recommendation.warnings || []).length > 0 && (
            <p className="text-xs text-muted-foreground">{recommendation.warnings.join(' ')}</p>
          )}
          {recommendation.products.map((item, index) => (
            <label key={index} className="flex flex-col gap-1 rounded border p-2 text-sm">
              <span className="flex items-center gap-2 font-medium">
                <input
                  type="checkbox"
                  checked={isPicked(index, item)}
                  onChange={(event) => setPicks((current) => ({ ...current, [index]: event.target.checked }))}
                  aria-label={`Track ${item.name}`}
                />
                {item.name} ({item.kind})
              </span>
              {(item.sources || []).map((source, sub) => (
                <span key={sub} className="flex items-center gap-2">
                  <Input
                    value={urls[sourceKey(index, sub)] ?? source.url}
                    onChange={(event) => setUrls((current) => ({ ...current, [sourceKey(index, sub)]: event.target.value }))}
                    aria-label={`Source URL for ${item.name}`}
                  />
                  <Badge variant="outline">{source.provenance}</Badge>
                </span>
              ))}
            </label>
          ))}
          <div className="flex flex-col gap-1">
            <Label htmlFor="chat-proposal-name">Watchlist name</Label>
            <Input id="chat-proposal-name" value={name} onChange={(event) => setName(event.target.value)} placeholder="TaskFlow pricing watch" />
          </div>
          <div>
            <Button type="button" onClick={handleCreate} disabled={saving}>
              {saving ? 'Creating…' : 'Create watchlist from picks'}
            </Button>
          </div>
        </div>
      )}
    </Card>
  );
}
