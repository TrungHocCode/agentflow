import { useEffect, useState } from 'react';
import { listChanges } from '../services/ciApi';
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
    <ul className="ci-list">
      {items.map((change) => (
        <li key={change.id} className="ci-row-card">
          <button type="button" className="ci-row-button" onClick={() => onSelect(change.id)}>
            <span className="flex min-w-0 flex-col gap-1">
              <span className="ci-row-title flex flex-wrap items-center gap-2">
                {change.section || 'Preamble'}
                <span className="ci-chip ci-chip--quiet">{change.kind}</span>
              </span>
              <span className="ci-row-meta truncate">
                {change.after_excerpt || change.before_excerpt}
              </span>
            </span>
            <OutcomeBadge outcome={change.kind === 'modified' ? 'changed' : change.kind} />
          </button>
        </li>
      ))}
    </ul>
  );
}
