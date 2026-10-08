import { useState } from 'react';
import { createWatchlist } from '../services/ciApi';
import { Alert, AlertDescription } from './ui/alert';
import { Button } from './ui/button';
import { Card } from './ui/card';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { Textarea } from './ui/textarea';

const DIMENSIONS = ['pricing', 'features', 'integrations', 'release_notes'];

export default function WatchlistSetup({ onCreated, onCancel }) {
  const [name, setName] = useState('');
  const [goal, setGoal] = useState('');
  const [dimensions, setDimensions] = useState(['pricing']);
  const [ownName, setOwnName] = useState('');
  const [ownWebsite, setOwnWebsite] = useState('');
  const [rivalName, setRivalName] = useState('');
  const [rivalWebsite, setRivalWebsite] = useState('');
  const [sourceUrl, setSourceUrl] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  function toggleDimension(dimension) {
    setDimensions((current) =>
      current.includes(dimension) ? current.filter((item) => item !== dimension) : [...current, dimension]
    );
  }

  async function handleSubmit(event) {
    event.preventDefault();
    setSaving(true);
    setError('');
    try {
      const watchlist = await createWatchlist({
        name,
        description: '',
        config: {
          goal,
          dimensions,
          workflow_version_id: null,
          comparison_criteria: [{ field: 'price', objective: 'Track public pricing changes' }],
          products: [
            { name: ownName, kind: 'own', official_website: ownWebsite || null },
            {
              name: rivalName,
              kind: 'competitor',
              official_website: rivalWebsite || null,
              sources: [{ url: sourceUrl, kind: dimensions[0] || 'pricing' }]
            }
          ]
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
          <Label htmlFor="ci-setup-name">Name</Label>
          <Input id="ci-setup-name" value={name} onChange={(event) => setName(event.target.value)} required />
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor="ci-setup-goal">Goal</Label>
          <Textarea
            id="ci-setup-goal"
            value={goal}
            onChange={(event) => setGoal(event.target.value)}
            required
            minLength={1}
          />
        </div>
        <fieldset className="flex flex-col gap-1">
          <legend className="text-sm font-medium">Dimensions</legend>
          <div className="flex flex-wrap gap-2">
            {DIMENSIONS.map((dimension) => (
              <label key={dimension} className="flex items-center gap-1 text-sm">
                <input
                  type="checkbox"
                  checked={dimensions.includes(dimension)}
                  onChange={() => toggleDimension(dimension)}
                />
                {dimension.replaceAll('_', ' ')}
              </label>
            ))}
          </div>
        </fieldset>
        <div className="grid grid-cols-2 gap-2">
          <div className="flex flex-col gap-1">
            <Label htmlFor="ci-setup-own">Own product</Label>
            <Input
              id="ci-setup-own"
              value={ownName}
              onChange={(event) => setOwnName(event.target.value)}
              required
              placeholder="Our product"
            />
          </div>
          <div className="flex flex-col gap-1">
            <Label htmlFor="ci-setup-own-site">Own website</Label>
            <Input
              id="ci-setup-own-site"
              value={ownWebsite}
              onChange={(event) => setOwnWebsite(event.target.value)}
              placeholder="https://ours.example/"
            />
          </div>
          <div className="flex flex-col gap-1">
            <Label htmlFor="ci-setup-rival">Competitor</Label>
            <Input
              id="ci-setup-rival"
              value={rivalName}
              onChange={(event) => setRivalName(event.target.value)}
              required
              placeholder="Rival product"
            />
          </div>
          <div className="flex flex-col gap-1">
            <Label htmlFor="ci-setup-rival-site">Competitor website</Label>
            <Input
              id="ci-setup-rival-site"
              value={rivalWebsite}
              onChange={(event) => setRivalWebsite(event.target.value)}
              placeholder="https://rival.example/"
            />
          </div>
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor="ci-setup-source">Approved source URL</Label>
          <Input
            id="ci-setup-source"
            value={sourceUrl}
            onChange={(event) => setSourceUrl(event.target.value)}
            required
            placeholder="https://rival.example/pricing"
          />
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
