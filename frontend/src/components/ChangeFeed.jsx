import { useEffect, useState } from 'react';
import { listChanges } from '../services/ciApi';
import { Card } from './ui/card';
import EmptyState from './EmptyState';
import ErrorNotice from './ErrorNotice';
import ListSkeleton from './ListSkeleton';
import { OutcomeBadge } from './OutcomeBadge';

export default function ChangeFeed({ watchlistId, onSelect }) {
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
        const page = await listChanges(watchlistId);
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
  }, [watchlistId, reloadKey]);

  if (loading) return <ListSkeleton label="Loading changes" rows={3} />;
  if (error) {
    return (
      <ErrorNotice
        message={error}
        onRetry={() => setReloadKey((key) => key + 1)}
        onDismiss={() => setError('')}
      />
    );
  }
  if (items.length === 0) return <EmptyState state="no_change" />;

  return (
    <ul className="flex flex-col gap-2">
      {items.map((change) => (
        <li key={change.id}>
          <Card className="p-3">
            <button type="button" className="flex w-full items-center justify-between gap-2 text-left" onClick={() => onSelect(change.id)}>
              <span>
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
  );
}
