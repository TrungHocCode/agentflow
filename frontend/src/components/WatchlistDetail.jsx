import { useEffect, useState } from 'react';
import { cancelRun, getRunDetails } from '../services/api';
import {
  approveRevision,
  buildBrief,
  getBrief,
  getWatchlist,
  listBriefs,
  listRevisions,
  listWatchlistRuns,
  startWatchlistRun
} from '../services/ciApi';
import { Alert, AlertDescription } from './ui/alert';
import { Button } from './ui/button';
import { Card } from './ui/card';
import { Spinner } from './ui/spinner';
import BriefView from './BriefView';
import ChangeDetail from './ChangeDetail';
import ChangeFeed from './ChangeFeed';
import EmptyState from './EmptyState';
import { OutcomeBadge } from './OutcomeBadge';

function runStateForBadge(status) {
  if (status === 'completed') return 'no_change';
  if (status === 'failed' || status === 'cancelled') return status === 'failed' ? 'unavailable' : 'partial';
  return 'baseline_created';
}

export default function WatchlistDetail({ watchlistId, onBack }) {
  const [watchlist, setWatchlist] = useState(null);
  const [revisions, setRevisions] = useState([]);
  const [runs, setRuns] = useState([]);
  const [briefs, setBriefs] = useState([]);
  const [selectedChangeId, setSelectedChangeId] = useState(null);
  const [selectedBrief, setSelectedBrief] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [action, setAction] = useState('');
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      setError('');
      try {
        const [detail, history, runPage, briefPage] = await Promise.all([
          getWatchlist(watchlistId),
          listRevisions(watchlistId),
          listWatchlistRuns(watchlistId),
          listBriefs(watchlistId)
        ]);
        if (cancelled) return;
        setWatchlist(detail);
        setRevisions(history || []);
        setRuns(runPage.items || []);
        setBriefs(briefPage.items || []);
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
  }, [watchlistId, refreshKey]);

  // Poll active runs so cancel/reload reflect progress without SSE wiring.
  useEffect(() => {
    const active = runs.some((run) => ['queued', 'running', 'pending'].includes(run.status));
    if (!active) return undefined;
    const timer = window.setInterval(async () => {
      try {
        const updated = await Promise.all(runs.map((run) => getRunDetails(run.run_id)));
        setRuns(updated);
      } catch {
        // Keep last known state; next tick retries.
      }
    }, 5000);
    return () => window.clearInterval(timer);
  }, [runs]);

  async function handleApprove() {
    setAction('approve');
    setError('');
    try {
      const revision = watchlist.current_revision;
      await approveRevision(watchlistId, revision.id, revision.config.workflow_version_id);
      setRefreshKey((key) => key + 1);
    } catch (err) {
      setError(err.message);
    } finally {
      setAction('');
    }
  }

  async function handleStartRun() {
    setAction('start');
    setError('');
    try {
      const revision = watchlist.current_revision;
      await startWatchlistRun(watchlistId, revision.id, revision.config.workflow_version_id);
      setRefreshKey((key) => key + 1);
    } catch (err) {
      setError(err.message);
    } finally {
      setAction('');
    }
  }

  async function handleCancelRun(runId) {
    setAction(`cancel-${runId}`);
    setError('');
    try {
      await cancelRun(runId);
      setRefreshKey((key) => key + 1);
    } catch (err) {
      setError(err.message);
    } finally {
      setAction('');
    }
  }

  async function handleBuildBrief(run) {
    setAction(`brief-${run.run_id}`);
    setError('');
    try {
      const brief = await buildBrief(watchlistId, run.run_id, run.watchlist_revision_id);
      setBriefs((current) => [brief, ...current]);
      setSelectedBrief(brief);
    } catch (err) {
      setError(err.message);
    } finally {
      setAction('');
    }
  }

  async function handleOpenBrief(briefId) {
    setError('');
    try {
      setSelectedBrief(await getBrief(briefId));
    } catch (err) {
      setError(err.message);
    }
  }

  if (loading) return <Spinner aria-label="Loading watchlist detail" />;
  if (error && !watchlist) {
    return (
      <Alert variant="destructive">
        <AlertDescription>{error}</AlertDescription>
      </Alert>
    );
  }
  if (!watchlist) return null;

  const revision = watchlist.current_revision;
  const approved = revision?.approval_status === 'approved';

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-2">
        <Button type="button" variant="ghost" onClick={onBack}>
          Back
        </Button>
        <h2 className="text-lg font-semibold">{watchlist.name}</h2>
      </div>
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      <Card className="flex flex-wrap gap-2 p-4">
        <Button type="button" onClick={handleApprove} disabled={action === 'approve' || approved}>
          {approved ? 'Revision approved' : 'Approve current revision'}
        </Button>
        <Button type="button" variant="outline" onClick={handleStartRun} disabled={action === 'start' || !approved}>
          Start run
        </Button>
      </Card>

      <Card className="p-4">
        <p className="mb-2 text-sm font-medium">Runs</p>
        {runs.length === 0 ? (
          <p className="text-sm text-muted-foreground">No runs yet.</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {runs.map((run) => (
              <li key={run.run_id} className="flex flex-wrap items-center gap-2 text-sm">
                <span className="font-mono text-xs">{run.run_id.slice(0, 8)}</span>
                <OutcomeBadge outcome={runStateForBadge(run.status)} />
                <span className="text-muted-foreground">{run.status}</span>
                <span className="flex-1" />
                {['queued', 'running', 'pending'].includes(run.status) && (
                  <Button
                    type="button"
                    variant="ghost"
                    onClick={() => handleCancelRun(run.run_id)}
                    disabled={action === `cancel-${run.run_id}`}
                  >
                    Cancel
                  </Button>
                )}
                {run.status === 'completed' && (
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => handleBuildBrief(run)}
                    disabled={action === `brief-${run.run_id}`}
                  >
                    Build brief
                  </Button>
                )}
                {run.status === 'failed' && <EmptyState state="failed" />}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card className="p-4">
        <p className="mb-2 text-sm font-medium">Changes</p>
        {selectedChangeId ? (
          <ChangeDetail changeId={selectedChangeId} onBack={() => setSelectedChangeId(null)} />
        ) : (
          <ChangeFeed watchlistId={watchlistId} onSelect={setSelectedChangeId} />
        )}
      </Card>

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
          <p className="text-sm text-muted-foreground">No briefs yet. Build one from a completed run.</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {briefs.map((brief) => (
              <li key={brief.id}>
                <button
                  type="button"
                  className="flex w-full items-center gap-2 text-left text-sm"
                  onClick={() => handleOpenBrief(brief.id)}
                >
                  <OutcomeBadge outcome={brief.outcome} />
                  <span className="text-muted-foreground">{brief.created_at}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card className="p-4">
        <p className="mb-2 text-sm font-medium">Revision history</p>
        <ul className="flex flex-col gap-1 text-sm">
          {revisions.map((item) => (
            <li key={item.id} className="flex items-center gap-2">
              <span className="font-mono text-xs">r{item.revision_number}</span>
              <OutcomeBadge outcome={item.approval_status === 'approved' ? 'baseline_created' : 'partial'} />
              <span className="text-muted-foreground">{item.approval_status}</span>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}
