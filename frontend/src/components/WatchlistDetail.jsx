import { useEffect, useState } from 'react';
import { ArrowLeft, Play } from 'lucide-react';
import { cancelRun, subscribeRunSSEStream } from '../services/api';
import {
  approveRevision,
  archiveWatchlist,
  buildBrief,
  createCollectionWorkflow,
  getModelHealth,
  getWatchlist,
  listBriefs,
  listRevisions,
  listWatchlistRuns,
  startWatchlistRun,
  updateWatchlist
} from '../services/ciApi';
import { Button } from './ui/button';
import { Card } from './ui/card';
import { Spinner } from './ui/spinner';
import { ConfirmDialog } from './ui/confirm-dialog';
import { Tabs, TabsContent, TabsList, TabsTrigger } from './ui/tabs';
import BriefView from './BriefView';
import ChangeDetail from './ChangeDetail';
import ChangeFeed from './ChangeFeed';
import ConfirmationBanner from './ConfirmationBanner';
import ErrorNotice from './ErrorNotice';
import LoadingState from './LoadingState';
import { ApprovalBadge, OutcomeBadge, QualityBadge, StatusBadge } from './OutcomeBadge';
import ScheduleSection from './ScheduleSection';
import TimelineAndAsk from './TimelineAndAsk';
import WatchlistScope from './WatchlistScope';
import { countLabel, dateLabel, revisionLabel, runLabel } from '../lib/labels';

const ACTIVE_RUN_STATUSES = new Set(['queued', 'running', 'pending']);

export default function WatchlistDetail({ watchlistId, onBack, onOpenRun }) {
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
  const [notice, setNotice] = useState('');
  const [archiveOpen, setArchiveOpen] = useState(false);
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
      // Stop before approving when the model is known to be down; if the check itself
      // cannot answer, proceed and let the run report what happened.
      const model = await getModelHealth().catch(() => null);
      if (model && model.status !== 'ok') {
        setError(model.message || 'The local model is unavailable.');
        return;
      }
      // Editing the watchlist to attach a workflow version creates a new revision,
      // so always re-read the current revision before approving or starting.
      let current = await getWatchlist(watchlistId);
      let revision = current.current_revision;
      let workflowVersionId = revision?.config?.workflow_version_id;
      if (!workflowVersionId) {
        const version = await createCollectionWorkflow(current.name);
        workflowVersionId = version.id;
        await updateWatchlist(watchlistId, {
          name: current.name,
          description: current.description || '',
          expected_revision_id: revision.id,
          config: { ...revision.config, workflow_version_id: workflowVersionId }
        });
        current = await getWatchlist(watchlistId);
        revision = current.current_revision;
        workflowVersionId = revision?.config?.workflow_version_id || workflowVersionId;
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

  async function handleArchive() {
    setArchiveOpen(false);
    setAction('archive');
    setError('');
    try {
      await archiveWatchlist(watchlistId);
      onBack();
    } catch (err) {
      setError(err.message);
      setAction('');
    }
  }

  function handleScopeSaved(updated) {
    setWatchlist(updated);
    setNotice('Saved as a new revision. It is approved when you next run it.');
    setRefreshKey((key) => key + 1);
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
      setNotice('Brief built.');
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

  if (loading) return <LoadingState label="Loading watchlist detail" />;
  if (error && !watchlist) {
    return (
      <ErrorNotice
        message={error}
        onRetry={() => setRefreshKey((key) => key + 1)}
        onDismiss={() => setError('')}
      />
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
    <div className="ci-page flex flex-col gap-4">
      <div className="ci-page-heading">
        <div className="ci-title-row">
          <Button type="button" variant="ghost" size="sm" onClick={onBack}>
            <ArrowLeft data-icon="inline-start" />Back
          </Button>
          <div>
            <p className="page-eyebrow">WATCHLIST</p>
            <h2 className="page-title">{watchlist.name}</h2>
          </div>
          <StatusBadge status={watchlist.status} />
        </div>
      </div>
      {error && (
        <ErrorNotice message={error} onDismiss={() => setError('')} />
      )}
      {notice && (
        <ConfirmationBanner message={notice} onDismiss={() => setNotice('')} />
      )}

      <Tabs value={tab} onValueChange={setTab} className="ci-tabs">
        <TabsList aria-label="Watchlist sections">
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="changes">Changes</TabsTrigger>
          <TabsTrigger value="briefs">Briefs</TabsTrigger>
          <TabsTrigger value="settings">Settings</TabsTrigger>
        </TabsList>

        <TabsContent value="overview" className="flex flex-col gap-4">
          <Card className="ci-section p-4">
            <div className="ci-section-head">
              <p className="ci-section-title">Tracking goal</p>
              <div className="ci-chip-row">
                <span className="ci-chip ci-chip--quiet">{countLabel(productCount, 'product')}</span>
                <span className="ci-chip ci-chip--quiet">{countLabel(sourceCount, 'source')}</span>
              </div>
            </div>
            <p className="ci-row-copy">{config.goal || 'No goal set for this revision.'}</p>
            <div className="ci-chip-row">
              <span className="ci-chip">{revisionLabel(revision)}</span>
              <ApprovalBadge status={revision?.approval_status} />
            </div>
          </Card>

          <WatchlistScope watchlist={watchlist} onSaved={handleScopeSaved} />

          <Card className="ci-section p-4">
            <div className="ci-section-head">
              <div>
                <p className="ci-section-title">Run the current revision</p>
                <p className="ci-section-caption">
                  {approved
                    ? 'Runs the current tracked sources and prepares a brief.'
                    : 'Running approves the products and sources listed above, then captures them and prepares a brief.'}
                </p>
              </div>
              <div className="ci-heading-actions">
                <Button type="button" onClick={handleRunNow} disabled={action === 'run' || Boolean(activeRun)}>
                  {action === 'run'
                    ? <Spinner data-icon="inline-start" aria-hidden="true" />
                    : <Play data-icon="inline-start" />}
                  {action === 'run'
                    ? 'Starting…'
                    : activeRun ? 'Run in progress…' : approved ? 'Run now' : 'Approve & run'}
                </Button>
                {activeRun && (
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => handleCancelRun(activeRun.run_id)}
                    disabled={action === `cancel-${activeRun.run_id}`}
                  >
                    {action === `cancel-${activeRun.run_id}` && <Spinner data-icon="inline-start" aria-hidden="true" />}
                    Cancel run
                  </Button>
                )}
              </div>
            </div>
          </Card>

          <Card className="ci-section p-4">
            <div className="ci-section-head">
              <p className="ci-section-title">Latest brief</p>
              <div className="ci-chip-row">
                {latestBrief && <OutcomeBadge outcome={latestBrief.outcome} />}
                {latestBrief && <QualityBadge quality={latestBrief.quality} />}
              </div>
            </div>
            {latestBrief ? (
              <div className="flex flex-col gap-2">
                <p className="ci-row-copy">{latestBrief.summary}</p>
                <div>
                  <Button type="button" variant="outline" onClick={() => handleOpenBrief(latestBrief)}>
                    Open brief
                  </Button>
                </div>
              </div>
            ) : (
              <p className="ci-empty empty-state-fade">
                No brief yet. A brief is prepared automatically when a run completes.
              </p>
            )}
          </Card>

          <Card className="ci-section p-4">
            <div className="ci-section-head">
              <p className="ci-section-title">Recent runs</p>
              <span className="ci-chip ci-chip--quiet">{runs.length} total</span>
            </div>
            {runs.length === 0 ? (
              <p className="ci-empty empty-state-fade">No runs yet.</p>
            ) : (
              <ul className="ci-list">
                {runs.slice(0, 5).map((run) => (
                  <li key={run.run_id} className="ci-row-card flex flex-wrap items-center gap-2">
                    <span className="tnum text-sm">{runLabel(run)}</span>
                    <StatusBadge status={run.status} />
                    <span className="flex-1" />
                    {onOpenRun && !ACTIVE_RUN_STATUSES.has(run.status) && (
                      <Button type="button" variant="ghost" size="sm" onClick={() => onOpenRun(run)}>
                        View
                      </Button>
                    )}
                    {run.error_message && (
                      <p className="ci-row-meta w-full">{run.error_message}</p>
                    )}
                    {ACTIVE_RUN_STATUSES.has(run.status) && (
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        onClick={() => handleCancelRun(run.run_id)}
                        disabled={action === `cancel-${run.run_id}`}
                      >
                        {action === `cancel-${run.run_id}` && <Spinner data-icon="inline-start" aria-hidden="true" />}
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
          <Card className="ci-section p-4">
            {selectedChangeId ? (
              <ChangeDetail changeId={selectedChangeId} onBack={() => setSelectedChangeId(null)} />
            ) : (
              <ChangeFeed watchlistId={watchlistId} onSelect={setSelectedChangeId} />
            )}
          </Card>
        </TabsContent>

        <TabsContent value="briefs">
          <Card className="ci-section p-4">
            {selectedBrief ? (
              <div className="flex flex-col gap-2">
                <Button type="button" variant="ghost" size="sm" onClick={() => setSelectedBrief(null)}>
                  <ArrowLeft data-icon="inline-start" />Back to list
                </Button>
                <BriefView brief={selectedBrief} />
              </div>
            ) : briefs.length === 0 ? (
              <p className="ci-empty empty-state-fade">
                No briefs yet. A brief is prepared automatically when a run completes.
              </p>
            ) : (
              <ul className="ci-list">
                {briefs.map((brief) => (
                  <li key={brief.id} className="ci-row-card">
                    <button
                      type="button"
                      className="ci-row-button"
                      onClick={() => handleOpenBrief(brief)}
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
        </TabsContent>

        <TabsContent value="settings" className="flex flex-col gap-4">
          <Card className="ci-section p-4">
            <div className="ci-section-head">
              <p className="ci-section-title">Current revision</p>
              <div className="ci-chip-row">
                <span className="ci-chip">{revisionLabel(revision)}</span>
                <ApprovalBadge status={revision?.approval_status} />
              </div>
            </div>
            {!approved && revision?.config?.workflow_version_id && (
              <div className="ci-heading-actions">
                <Button type="button" variant="outline" onClick={handleApprove} disabled={action === 'approve'}>
                  {action === 'approve' && <Spinner data-icon="inline-start" aria-hidden="true" />}
                  {action === 'approve' ? 'Approving…' : 'Approve current revision'}
                </Button>
              </div>
            )}
          </Card>

          <Card className="ci-section p-4">
            <p className="ci-section-title">Source fetches without a brief</p>
            {runs.filter((run) => run.status === 'completed' && !briefs.some((brief) => brief.run_id === run.run_id)).length === 0 ? (
              <p className="ci-empty empty-state-fade">Every completed run has a brief.</p>
            ) : (
              <ul className="ci-list">
                {runs
                  .filter((run) => run.status === 'completed' && !briefs.some((brief) => brief.run_id === run.run_id))
                  .map((run) => (
                    <li key={run.run_id} className="ci-row-card flex flex-wrap items-center gap-2">
                      <span className="tnum text-sm">{runLabel(run)}</span>
                      <span className="flex-1" />
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() => handleBuildBrief(run)}
                        disabled={action === `brief-${run.run_id}`}
                      >
                        {action === `brief-${run.run_id}` && <Spinner data-icon="inline-start" aria-hidden="true" />}
                        {action === `brief-${run.run_id}` ? 'Building…' : 'Build brief'}
                      </Button>
                    </li>
                  ))}
              </ul>
            )}
          </Card>

          <Card className="ci-section p-4">
            <p className="ci-section-title">Revision history</p>
            <ul className="ci-list">
              {revisions.map((item) => (
                <li key={item.id} className="ci-row-card flex flex-wrap items-center gap-2">
                  <span className="text-sm">{revisionLabel(item)}</span>
                  <ApprovalBadge status={item.approval_status} />
                </li>
              ))}
            </ul>
          </Card>

          <Card className="ci-section p-4">
            <p className="ci-section-title">Schedules</p>
            <ScheduleSection watchlistId={watchlistId} />
          </Card>

          <Card className="ci-section p-4">
            <p className="ci-section-title">Timeline and history Q&amp;A</p>
            <TimelineAndAsk watchlistId={watchlistId} />
          </Card>

          <Card className="ci-section p-4">
            <div className="ci-section-head">
              <div>
                <p className="ci-section-title">Archive this watchlist</p>
                <p className="ci-section-caption">
                  Stops future runs and hides it from your lists. Its history is kept.
                </p>
              </div>
              <Button
                type="button"
                variant="outline"
                onClick={() => setArchiveOpen(true)}
                disabled={action === 'archive' || Boolean(activeRun)}
              >
                {action === 'archive' && <Spinner data-icon="inline-start" aria-hidden="true" />}
                Archive
              </Button>
            </div>
          </Card>
        </TabsContent>
      </Tabs>
      <ConfirmDialog
        open={archiveOpen}
        title="Archive this watchlist?"
        description={`"${watchlist.name}" stops running and leaves your lists. Its history is kept, but it cannot be restored from here.`}
        confirmLabel="Archive"
        tone="destructive"
        onConfirm={handleArchive}
        onCancel={() => setArchiveOpen(false)}
      />
    </div>
  );
}
