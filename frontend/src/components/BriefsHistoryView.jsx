import { useEffect, useState } from 'react';
import { ArrowLeft } from 'lucide-react';
import { getRuns } from '../services/api';
import { listBriefs, listWatchlists } from '../services/ciApi';
import { Button } from './ui/button';
import { Card } from './ui/card';
import BriefView from './BriefView';
import ErrorNotice from './ErrorNotice';
import ListSkeleton from './ListSkeleton';
import { OutcomeBadge, QualityBadge, StatusBadge } from './OutcomeBadge';
import { dateLabel, runLabel } from '../lib/labels';

// App-level brief archive plus run history across every watchlist.
export default function BriefsHistoryView({ onOpenRun }) {
  const [briefs, setBriefs] = useState([]);
  const [runs, setRuns] = useState([]);
  const [selectedBrief, setSelectedBrief] = useState(null);
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
        const briefPages = await Promise.all(watchlists.map((watchlist) => listBriefs(watchlist.id)));
        const mergedBriefs = briefPages
          .flatMap((page) => page.items || [])
          .sort((left, right) => String(right.created_at || '').localeCompare(String(left.created_at || '')));
        let runList = [];
        try {
          runList = await getRuns();
        } catch {
          runList = [];
        }
        if (!cancelled) {
          setBriefs(mergedBriefs);
          setRuns(runList || []);
        }
      } catch (err) {
        if (!cancelled) setError(err.message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => { cancelled = true; };
  }, [reloadKey]);

  if (loading) {
    return (
      <div className="page-frame">
        <ListSkeleton label="Loading briefs and history" rows={3} />
      </div>
    );
  }
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

  return (
    <div className="page-frame ci-page flex flex-col gap-4">
      <div className="ci-page-heading">
        <div className="page-heading__copy">
          <p className="page-eyebrow">INTELLIGENCE</p>
          <h2 className="page-title">Briefs &amp; History</h2>
          <p className="page-description">Read finished briefs and reopen the runs that produced them.</p>
        </div>
      </div>

      <Card className="ci-section p-4">
        <div className="ci-section-head">
          <p className="ci-section-title">Briefs</p>
          <span className="ci-chip ci-chip--quiet">{briefs.length} total</span>
        </div>
        {selectedBrief ? (
          <div className="flex flex-col gap-2">
            <div>
              <Button type="button" variant="ghost" size="sm" onClick={() => setSelectedBrief(null)}>
                <ArrowLeft data-icon="inline-start" />Back to list
              </Button>
            </div>
            <BriefView brief={selectedBrief} />
          </div>
        ) : briefs.length === 0 ? (
          <p className="ci-empty empty-state-fade">No briefs yet. A brief is prepared automatically when a run completes.</p>
        ) : (
          <ul className="ci-list">
            {briefs.map((brief) => (
              <li key={brief.id} className="ci-row-card">
                <button
                  type="button"
                  className="ci-row-button"
                  onClick={() => setSelectedBrief(brief)}
                >
                  <span className="ci-chip-row">
                    <OutcomeBadge outcome={brief.outcome} />
                    <QualityBadge quality={brief.quality} />
                  </span>
                  <span className="tnum text-sm text-muted-foreground">{dateLabel(brief.created_at)}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card className="ci-section p-4">
        <div className="ci-section-head">
          <p className="ci-section-title">Run history</p>
          <span className="ci-chip ci-chip--quiet">{runs.length} total</span>
        </div>
        {runs.length === 0 ? (
          <p className="ci-empty empty-state-fade">No runs yet.</p>
        ) : (
          <ul className="ci-list">
            {runs.map((run) => (
              <li key={run.run_id} className="ci-row-card flex flex-wrap items-center gap-2">
                <span className="tnum text-sm">{runLabel(run)}</span>
                <StatusBadge status={run.status} />
                <span className="flex-1" />
                {onOpenRun && (
                  <Button type="button" variant="ghost" size="sm" onClick={() => onOpenRun(run)}>
                    View
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
