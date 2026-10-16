import { useEffect, useState } from 'react';
import { Play } from 'lucide-react';
import { cancelRun, subscribeRunSSEStream } from '../services/api';
import {
  approveRevision,
  buildBrief,
  createCollectionWorkflow,
  getWatchlist,
  listBriefs,
  listRevisions,
  listWatchlistRuns,
  startWatchlistRun,
  updateWatchlist
} from '../services/ciApi';
import { Alert, AlertDescription } from './ui/alert';
import { Button } from './ui/button';
import { Card } from './ui/card';
import { Spinner } from './ui/spinner';
import { Tabs, TabsContent, TabsList, TabsTrigger } from './ui/tabs';
import BriefView from './BriefView';
import ChangeDetail from './ChangeDetail';
import ChangeFeed from './ChangeFeed';
import { ApprovalBadge, OutcomeBadge, QualityBadge, StatusBadge } from './OutcomeBadge';
import ScheduleSection from './ScheduleSection';
import TimelineAndAsk from './TimelineAndAsk';
import { dateLabel, revisionLabel, runLabel } from '../lib/labels';

const ACTIVE_RUN_STATUSES = new Set(['queued', 'running', 'pending']);

export default function WatchlistDetail({ watchlistId, onBack }) {
  const [watchlist, setWatchlist] = useState(null);
  const [revisions, setRevisions] = useState([]);
  const [runs, setRuns] = useState([]);
  const [briefs, setBriefs] = useState([]);
  const [selectedChangeId, setSelectedChangeId] = useState(null);
  const [selectedBrief, setSelectedBrief] = useState(null);
  const [tab, setTab] = useState('overview');
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

  const activeRunId = runs.find((run) => ACTIVE_RUN_STATUSES.has(run.status))?.run_id || null;

  // Stream the active run's progress instead of polling.
  useEffect(() => {
    if (!activeRunId) return undefined;
    let terminal = false;
    const unsubscribe = subscribeRunSSEStream(
      activeRunId,
      (event) => {
        const status = event.status || event.payload?.status;
        const isTerminal = ['run_completed', 'run_failed', 'run_interrupted'].includes(event.type);
        if (isTerminal) {
          const nextStatus = event.status
            || (event.type === 'run_failed' ? 'failed' : event.type === 'run_interrupted' ? 'interrupted' : 'completed');
          setRuns((previous) => previous.map((run) => run.run_id === activeRunId ? { ...run, status: nextStatus } : run));
        } else if (status === 'queued' || status === 'running') {
          setRuns((previous) => previous.map((run) => run.run_id === activeRunId ? { ...run, status } : run));
        }
        if (isTerminal && !terminal) {
          terminal = true;
          setRefreshKey((key) => key + 1);
        }
      },
      () => {
        // Fall back to a refresh if the stream drops while the run is active.
        if (!terminal) setRefreshKey((key) => key + 1);
      }
    );
    return () => unsubscribe();
  }, [activeRunId, refreshKey]);

  async function handleRunNow() {
    setAction('run');
    setError('');
    try {
      const revision = watchlist.current_revision;
      let workflowVersionId = revision?.config?.workflow_version_id;
      if (!workflowVersionId) {
        const version = await createCollectionWorkflow(watchlist.name);
        workflowVersionId = version.id;
        await updateWatchlist(watchlistId, {
          name: watchlist.name,
          description: watchlist.description || '',
          expected_revision_id: revision.id,
          config: { ...revision.config, workflow_version_id: workflowVersionId }
        });
      }
      if (revision.approval_status !== 'approved') {
        await approveRevision(watchlistId, revision.id, workflowVersionId);
      }
      await startWatchlistRun(watchlistId, revision.id, workflowVersionId);
      setRefreshKey((key) => key + 1);
    } catch (err) {
      setError(err.message);
    } finally {
      setAction('');
    }
  }

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
      setTab('briefs');
    } catch (err) {
      setError(err.message);
    } finally {
      setAction('');
    }
  }

  async function handleOpenBrief(brief) {
    setError('');
    setSelectedBrief(brief);
    setTab('briefs');
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
  const config = revision?.config || {};
  const productCount = (config.products || []).length;
  const sourceCount = (config.products || []).reduce((total, product) => total + ((product.sources || []).length), 0);
  const latestBrief = briefs[0] || null;
  const activeRun = runs.find((run) => ACTIVE_RUN_STATUSES.has(run.status));

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-2">
        <Button type="button" variant="ghost" onClick={onBack}>
          Back
        </Button>
        <h2 className="text-lg font-semibold">{watchlist.name}</h2>
        <StatusBadge status={watchlist.status} />
      </div>
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList aria-label="Watchlist sections">
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="changes">Changes</TabsTrigger>
          <TabsTrigger value="briefs">Briefs</TabsTrigger>
          <TabsTrigger value="settings">Settings</TabsTrigger>
        </TabsList>

        <TabsContent value="overview" className="flex flex-col gap-4">
          <Card className="p-4">
            <p className="mb-2 text-sm font-medium">Tracking goal</p>
            <p className="text-sm">{config.goal || 'No goal set for this revision.'}</p>
            <p className="mt-2 text-sm text-muted-foreground">
              {productCount} products · {sourceCount} sources ·{' '}
              {revisionLabel(revision)} · {revision?.approval_status}
            </p>
          </Card>

          <Card className="flex flex-wrap items-center gap-2 p-4">
            <Button type="button" onClick={handleRunNow} disabled={action === 'run' || Boolean(activeRun)}>
              <Play data-icon="inline-start" />
              {action === 'run' ? 'Starting…' : activeRun ? 'Run in progress…' : 'Run now'}
            </Button>
            {activeRun && (
              <Button
                type="button"
                variant="outline"
                onClick={() => handleCancelRun(activeRun.run_id)}
                disabled={action === `cancel-${activeRun.run_id}`}
              >
                Cancel run
              </Button>
            )}
            <span className="text-sm text-muted-foreground">
              Runs the current tracked sources and prepares a brief.
            </span>
          </Card>

          <Card className="p-4">
            <div className="mb-2 flex items-center gap-2">
              <p className="text-sm font-medium">Latest brief</p>
              {latestBrief && <OutcomeBadge outcome={latestBrief.outcome} />}
              {latestBrief && <QualityBadge quality={latestBrief.quality} />}
            </div>
            {latestBrief ? (
              <div className="flex flex-col gap-2">
                <p className="text-sm">{latestBrief.summary}</p>
                <div>
                  <Button type="button" variant="outline" onClick={() => handleOpenBrief(latestBrief)}>
                    Open brief
                  </Button>
                </div>
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">
                No brief yet. A brief is prepared automatically when a run completes.
              </p>
            )}
          </Card>

          <Card className="p-4">
            <p className="mb-2 text-sm font-medium">Recent runs</p>
            {runs.length === 0 ? (
              <p className="text-sm text-muted-foreground">No runs yet.</p>
            ) : (
              <ul className="flex flex-col gap-2">
                {runs.slice(0, 5).map((run) => (
                  <li key={run.run_id} className="flex flex-wrap items-center gap-2 text-sm">
                    <span>{runLabel(run)}</span>
                    <StatusBadge status={run.status} />
                    <span className="flex-1" />
                    {ACTIVE_RUN_STATUSES.has(run.status) && (
                      <Button
                        type="button"
                        variant="ghost"
                        onClick={() => handleCancelRun(run.run_id)}
                        disabled={action === `cancel-${run.run_id}`}
                      >
                        Cancel
                      </Button>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </TabsContent>

        <TabsContent value="changes">
          <Card className="p-4">
            {selectedChangeId ? (
              <ChangeDetail changeId={selectedChangeId} onBack={() => setSelectedChangeId(null)} />
            ) : (
              <ChangeFeed watchlistId={watchlistId} onSelect={setSelectedChangeId} />
            )}
          </Card>
        </TabsContent>

        <TabsContent value="briefs">
          <Card className="p-4">
            {selectedBrief ? (
              <div className="flex flex-col gap-2">
                <Button type="button" variant="ghost" onClick={() => setSelectedBrief(null)}>
                  Back to list
                </Button>
                <BriefView brief={selectedBrief} />
              </div>
            ) : briefs.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                No briefs yet. A brief is prepared automatically when a run completes.
              </p>
            ) : (
              <ul className="flex flex-col gap-2">
                {briefs.map((brief) => (
                  <li key={brief.id}>
                    <button
                      type="button"
                      className="flex w-full items-center gap-2 text-left text-sm"
                      onClick={() => handleOpenBrief(brief)}
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
        </TabsContent>

        <TabsContent value="settings" className="flex flex-col gap-4">
          <Card className="p-4">
            <p className="mb-2 text-sm font-medium">Current revision</p>
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <span>{revisionLabel(revision)}</span>
              <ApprovalBadge status={revision?.approval_status} />
              <span className="flex-1" />
              {!approved && (
                <Button type="button" variant="outline" onClick={handleApprove} disabled={action === 'approve'}>
                  {action === 'approve' ? 'Approving…' : 'Approve current revision'}
                </Button>
              )}
            </div>
          </Card>

          <Card className="p-4">
            <p className="mb-2 text-sm font-medium">Source fetches without a brief</p>
            {runs.filter((run) => run.status === 'completed' && !briefs.some((brief) => brief.run_id === run.run_id)).length === 0 ? (
              <p className="text-sm text-muted-foreground">Every completed run has a brief.</p>
            ) : (
              <ul className="flex flex-col gap-2">
                {runs
                  .filter((run) => run.status === 'completed' && !briefs.some((brief) => brief.run_id === run.run_id))
                  .map((run) => (
                    <li key={run.run_id} className="flex flex-wrap items-center gap-2 text-sm">
                      <span>{runLabel(run)}</span>
                      <span className="flex-1" />
                      <Button
                        type="button"
                        variant="outline"
                        onClick={() => handleBuildBrief(run)}
                        disabled={action === `brief-${run.run_id}`}
                      >
                        Build brief
                      </Button>
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
                  <span>{revisionLabel(item)}</span>
                  <ApprovalBadge status={item.approval_status} />
                </li>
              ))}
            </ul>
          </Card>

          <Card className="p-4">
            <p className="mb-2 text-sm font-medium">Schedules</p>
            <ScheduleSection watchlistId={watchlistId} />
          </Card>

          <Card className="p-4">
            <p className="mb-2 text-sm font-medium">Timeline and history Q&A</p>
            <TimelineAndAsk watchlistId={watchlistId} />
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
