import { useEffect, useState } from 'react';
import { Plus } from 'lucide-react';
import { listWatchlists } from '../services/ciApi';
import { Button } from './ui/button';
import { Card } from './ui/card';
import ErrorNotice from './ErrorNotice';
import ListSkeleton from './ListSkeleton';
import { StatusBadge } from './OutcomeBadge';
import { revisionLabel } from '../lib/labels';

export default function WatchlistList({ onSelect, onCreate }) {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [reloadKey, setReloadKey] = useState(0);

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
  }, [reloadKey]);

  if (loading) return <ListSkeleton label="Loading watchlists" rows={4} />;
  if (error) {
    return (
      <ErrorNotice
        message={error}
        onRetry={() => setReloadKey((key) => key + 1)}
        onDismiss={() => setError('')}
      />
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h2 className="type-display">Watchlists</h2>
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
                    <span className="type-display block">{watchlist.name}</span>
                    <span className="type-caption tnum block">
                      {revisionLabel(watchlist.current_revision)} ·{' '}
                      {watchlist.current_revision?.approval_status}
                    </span>
                  </span>
                  <StatusBadge status={watchlist.status} />
                </button>
              </Card>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
