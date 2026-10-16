import { useEffect, useState } from 'react';
import { listChanges, listWatchlists } from '../services/ciApi';
import { Alert, AlertDescription } from './ui/alert';
import { Card } from './ui/card';
import { Spinner } from './ui/spinner';
import ChangeDetail from './ChangeDetail';
import EmptyState from './EmptyState';
import { OutcomeBadge } from './OutcomeBadge';

// App-level change feed across every watchlist the user owns.
export default function ChangesView() {
  const [items, setItems] = useState([]);
  const [selectedChangeId, setSelectedChangeId] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      setError('');
      try {
        const watchlists = (await listWatchlists()).items || [];
        const pages = await Promise.all(watchlists.map(async (watchlist) => {
          const page = await listChanges(watchlist.id);
          return (page.items || []).map((change) => ({ ...change, watchlistName: watchlist.name }));
        }));
        const merged = pages
          .flat()
          .sort((left, right) => String(right.detected_at || '').localeCompare(String(left.detected_at || '')));
        if (!cancelled) setItems(merged);
      } catch (err) {
        if (!cancelled) setError(err.message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => { cancelled = true; };
  }, []);

  if (loading) return <Spinner aria-label="Loading changes" />;
  if (error) {
    return (
      <Alert variant="destructive">
        <AlertDescription>{error}</AlertDescription>
      </Alert>
    );
  }
  if (selectedChangeId) {
    return <div className="page-frame"><ChangeDetail changeId={selectedChangeId} onBack={() => setSelectedChangeId(null)} /></div>;
  }
  if (items.length === 0) {
    return <div className="page-frame"><EmptyState state="no_change" /></div>;
  }

  return (
    <div className="page-frame flex flex-col gap-3">
      <h2 className="text-lg font-semibold">Changes</h2>
      <ul className="flex flex-col gap-2">
        {items.map((change) => (
          <li key={change.id}>
            <Card className="p-3">
              <button
                type="button"
                className="flex w-full items-center justify-between gap-2 text-left"
                onClick={() => setSelectedChangeId(change.id)}
              >
                <span>
                  <span className="block text-sm text-muted-foreground">{change.watchlistName}</span>
                  <span className="block font-medium">{change.section || 'Preamble'} · {change.kind}</span>
                  <span className="block truncate text-sm text-muted-foreground">
                    {change.after_excerpt || change.before_excerpt}
                  </span>
                </span>
                <OutcomeBadge outcome={change.kind === 'modified' ? 'changed' : change.kind} />
              </button>
            </Card>
          </li>
        ))}
      </ul>
    </div>
  );
}
