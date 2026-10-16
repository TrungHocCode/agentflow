import { useEffect, useState } from 'react';
import { getRuns } from '../services/api';
import { listBriefs, listWatchlists } from '../services/ciApi';
import { Alert, AlertDescription } from './ui/alert';
import { Button } from './ui/button';
import { Card } from './ui/card';
import { Spinner } from './ui/spinner';
import BriefView from './BriefView';
import { OutcomeBadge, QualityBadge, StatusBadge } from './OutcomeBadge';
import { dateLabel, runLabel } from '../lib/labels';

// App-level brief archive plus run history across every watchlist.
export default function BriefsHistoryView({ onOpenRun }) {
  const [briefs, setBriefs] = useState([]);
  const [runs, setRuns] = useState([]);
  const [selectedBrief, setSelectedBrief] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

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
  }, []);

  if (loading) return <Spinner aria-label="Loading briefs and history" />;
  if (error) {
    return (
      <Alert variant="destructive">
        <AlertDescription>{error}</AlertDescription>
      </Alert>
    );
  }

  return (
    <div className="page-frame flex flex-col gap-4">
      <h2 className="text-lg font-semibold">Briefs &amp; History</h2>

      <Card className="p-4">
        <p className="mb-2 text-sm font-medium">Briefs</p>
        {selectedBrief ? (
          <div className="flex flex-col gap-2">
            <Button type="button" variant="ghost" onClick={() => setSelectedBrief(null)}>
              Back to list
            </Button>
            <BriefView brief={selectedBrief} />
          </div>
        ) : briefs.length === 0 ? (
          <p className="text-sm text-muted-foreground">No briefs yet. A brief is prepared automatically when a run completes.</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {briefs.map((brief) => (
              <li key={brief.id}>
                <button
                  type="button"
                  className="flex w-full items-center gap-2 text-left text-sm"
                  onClick={() => setSelectedBrief(brief)}
                >
                  <OutcomeBadge outcome={brief.outcome} />
                  <QualityBadge quality={brief.quality} />
                  <span className="text-muted-foreground">{dateLabel(brief.created_at)}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card className="p-4">
        <p className="mb-2 text-sm font-medium">Run history</p>
        {runs.length === 0 ? (
          <p className="text-sm text-muted-foreground">No runs yet.</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {runs.map((run) => (
              <li key={run.run_id} className="flex flex-wrap items-center gap-2 text-sm">
                <span>{runLabel(run)}</span>
                <StatusBadge status={run.status} />
                <span className="flex-1" />
                {onOpenRun && (
                  <Button type="button" variant="ghost" onClick={() => onOpenRun(run)}>
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
