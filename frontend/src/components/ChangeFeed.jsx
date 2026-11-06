import { useEffect, useState } from 'react';
import { listBriefs, listChanges, listWatchlistRuns } from '../services/ciApi';
import { changeStateFor } from '../lib/changeState';
import EmptyState from './EmptyState';
import ErrorNotice from './ErrorNotice';
import ListSkeleton from './ListSkeleton';
import { OutcomeBadge } from './OutcomeBadge';

export default function ChangeFeed({ watchlistId, onSelect }) {
  const [items, setItems] = useState([]);
  const [emptyState, setEmptyState] = useState({ state: 'unavailable' });
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
        const changes = page.items || [];
        // Only an empty feed needs explaining, so skip these requests when there are changes.
        const [runs, briefs] = changes.length > 0
          ? [null, null]
          : await Promise.all([listWatchlistRuns(watchlistId), listBriefs(watchlistId)]);
        if (!cancelled) {
          setItems(changes);
          if (runs) setEmptyState(changeStateFor(runs.items || [], briefs.items || []));
        }
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
  if (items.length === 0) return <EmptyState state={emptyState.state} detail={emptyState.detail} />;

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
