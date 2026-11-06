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
        if (!cancelled) setItems((page.items || []).filter((item) => item.status !== 'archived'));
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
    <div className="ci-page flex flex-col gap-4">
      <div className="ci-page-heading">
        <div className="page-heading__copy">
          <p className="page-eyebrow">INTELLIGENCE</p>
          <h2 className="page-title">Watchlists</h2>
          <p className="page-description">Track competitor pages and turn real changes into a reviewable brief.</p>
        </div>
        <Button type="button" onClick={onCreate}>
          <Plus data-icon="inline-start" /> New watchlist
        </Button>
      </div>
      {items.length === 0 ? (
        <Card className="empty-state empty-state-fade">
          <p className="empty-state__title">No watchlists yet</p>
          <p className="empty-state__description">Create one to start tracking competitor changes.</p>
          <Button type="button" onClick={onCreate}>
            <Plus data-icon="inline-start" /> New watchlist
          </Button>
        </Card>
      ) : (
        <ul className="ci-list">
          {items.map((watchlist) => (
            <li key={watchlist.id}>
              <Card className="p-4">
                <button
                  type="button"
                  className="ci-row-button"
                  onClick={() => onSelect(watchlist.id)}
                >
                  <span className="flex min-w-0 flex-col gap-1">
                    <span className="ci-row-title">{watchlist.name}</span>
                    <span className="ci-row-meta">
                      <span className="ci-chip ci-chip--quiet">{revisionLabel(watchlist.current_revision)}</span>
                      <span className="ci-chip ci-chip--quiet">{watchlist.current_revision?.approval_status}</span>
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
