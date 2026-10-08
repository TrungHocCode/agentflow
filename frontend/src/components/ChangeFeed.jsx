import { useEffect, useState } from 'react';
import { listChanges } from '../services/ciApi';
import { Alert, AlertDescription } from './ui/alert';
import { Card } from './ui/card';
import { Spinner } from './ui/spinner';
import EmptyState from './EmptyState';
import { OutcomeBadge } from './OutcomeBadge';

export default function ChangeFeed({ watchlistId, onSelect }) {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

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
  }, [watchlistId]);

  if (loading) return <Spinner aria-label="Loading changes" />;
  if (error) {
    return (
      <Alert variant="destructive">
        <AlertDescription>{error}</AlertDescription>
      </Alert>
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
