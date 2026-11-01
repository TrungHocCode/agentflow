import { useEffect, useState } from 'react';
import { listChanges, listWatchlists } from '../services/ciApi';
import ChangeDetail from './ChangeDetail';
import EmptyState from './EmptyState';
import ErrorNotice from './ErrorNotice';
import ListSkeleton from './ListSkeleton';
import { OutcomeBadge } from './OutcomeBadge';

// App-level change feed across every watchlist the user owns.
export default function ChangesView() {
  const [items, setItems] = useState([]);
  const [selectedChangeId, setSelectedChangeId] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [reloadKey, setReloadKey] = useState(0);

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
  }, [reloadKey]);

  if (loading) return <div className="page-frame"><ListSkeleton label="Loading changes" rows={4} /></div>;
  if (error) {
    return (
      <div className="page-frame">
        <ErrorNotice
          message={error}
          onRetry={() => setReloadKey((key) => key + 1)}
          onDismiss={() => setError('')}
        />
      </div>
    );
  }
  if (selectedChangeId) {
    return <div className="page-frame"><ChangeDetail changeId={selectedChangeId} onBack={() => setSelectedChangeId(null)} /></div>;
  }
  if (items.length === 0) {
    return <div className="page-frame"><EmptyState state="no_change" /></div>;
  }

  return (
    <div className="page-frame ci-page flex flex-col gap-3">
      <div className="ci-page-heading">
        <div className="page-heading__copy">
          <p className="page-eyebrow">INTELLIGENCE</p>
          <h2 className="page-title">Changes</h2>
          <p className="page-description">Every detected movement across the watchlists you own.</p>
        </div>
      </div>
      <ul className="ci-list">
        {items.map((change) => (
          <li key={change.id} className="ci-row-card">
            <button
              type="button"
              className="ci-row-button"
              onClick={() => setSelectedChangeId(change.id)}
            >
              <span className="flex min-w-0 flex-col gap-1">
                <span className="ci-row-meta">
                  <span className="ci-chip ci-chip--quiet">{change.watchlistName}</span>
                </span>
                <span className="ci-row-title flex flex-wrap items-center gap-2">
                  {change.section || 'Preamble'}
                  <span className="ci-chip ci-chip--quiet">{change.kind}</span>
                </span>
                <span className="ci-row-meta truncate">{change.after_excerpt || change.before_excerpt}</span>
              </span>
              <OutcomeBadge outcome={change.kind === 'modified' ? 'changed' : change.kind} />
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
