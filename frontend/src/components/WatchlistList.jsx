import { useEffect, useState } from 'react';
import { Plus } from 'lucide-react';
import { listWatchlists } from '../services/ciApi';
import { Alert, AlertDescription } from './ui/alert';
import { Button } from './ui/button';
import { Card } from './ui/card';
import { Spinner } from './ui/spinner';
import { OutcomeBadge } from './OutcomeBadge';

export default function WatchlistList({ onSelect, onCreate }) {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      setError('');
      try {
        const page = await listWatchlists();
        if (!cancelled) setItems(page.items || []);
      } catch (err) {
        if (!cancelled) setError(err.message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, []);

  if (loading) return <Spinner aria-label="Loading watchlists" />;
  if (error) {
    return (
      <Alert variant="destructive">
        <AlertDescription>{error}</AlertDescription>
      </Alert>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">Watchlists</h2>
        <Button type="button" onClick={onCreate}>
          <Plus data-icon="inline-start" /> New watchlist
        </Button>
      </div>
      {items.length === 0 ? (
        <Card className="p-6 text-center text-sm text-muted-foreground">
          No watchlists yet. Create one to start tracking competitor changes.
        </Card>
      ) : (
        <ul className="flex flex-col gap-2">
          {items.map((watchlist) => (
            <li key={watchlist.id}>
              <Card className="p-4">
                <button
                  type="button"
                  className="flex w-full items-center justify-between gap-2 text-left"
                  onClick={() => onSelect(watchlist.id)}
                >
                  <span>
                    <span className="block font-medium">{watchlist.name}</span>
                    <span className="block text-sm text-muted-foreground">
                      Revision {watchlist.current_revision?.revision_number} ·{' '}
                      {watchlist.current_revision?.approval_status}
                    </span>
                  </span>
                  <OutcomeBadge outcome={watchlist.status === 'active' ? 'baseline_created' : 'unavailable'} />
                </button>
              </Card>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
