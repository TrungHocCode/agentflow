import { useEffect, useState } from 'react';
import { Activity, Clock3, Download, FileText, Timer, XCircle } from 'lucide-react';
import StructuredText from './StructuredText';
import TaskStatusBadge from './TaskStatusBadge';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from './ui/card';
import { Spinner } from './ui/spinner';

const STATUS_LABELS = {
  pending: 'Pending approval',
  created: 'Created',
  waiting_for_approval: 'Pending approval',
  queued: 'Queued',
  running: 'Running',
  completed: 'Completed',
  failed: 'Failed',
  cancelled: 'Cancelled',
  interrupted: 'Interrupted',
  abandoned: 'Stopped'
};

function resultText(result) {
  if (typeof result?.result === 'object') return JSON.stringify(result.result, null, 2);
  return String(result?.result ?? (typeof result === 'object' ? JSON.stringify(result, null, 2) : result));
}

export default function ExecutionTracker({
  currentRun,
  results,
  plan,
  isStreaming,
  executionDuration,
  onOpenResults,
  onCancelRun,
  isCancelling
}) {
  const [liveSeconds, setLiveSeconds] = useState(0);
  const canCancel = ['queued', 'running'].includes(currentRun?.status);
  const finishedCount = (plan || []).filter(task => ['done', 'success', 'completed', 'partial', 'failed', 'skipped', 'cancelled', 'interrupted'].includes(String(task?.status || '').toLowerCase())).length;
  const statusTone = ['failed', 'interrupted'].includes(currentRun?.status)
    ? 'failed'
    : currentRun?.status === 'completed' ? 'done'
      : ['queued', 'running'].includes(currentRun?.status) ? 'running' : 'pending';

  useEffect(() => {
    if (!isStreaming) {
      setLiveSeconds(0);
      return undefined;
    }
    const start = Date.now();
    const timer = window.setInterval(() => setLiveSeconds(((Date.now() - start) / 1000).toFixed(1)), 250);
    return () => window.clearInterval(timer);
  }, [isStreaming]);

  return (
    <section className="page-frame" aria-labelledby="runs-title">
      <div className="page-heading">
        <div className="page-heading__copy">
          <p className="page-eyebrow">RESEARCH RUNS</p>
          <h1 id="runs-title" className="page-title">Workflow progress</h1>
          <p className="page-description">
            {currentRun ? STATUS_LABELS[currentRun.status] || 'Updating status' : 'No run selected.'}
          </p>
        </div>
        <div className="run-heading-actions">
          {currentRun && (
            <Badge variant={statusTone === 'failed' ? 'destructive' : 'secondary'} className={`run-status run-status--${statusTone}`}>
              {isStreaming && <Spinner data-icon="inline-start" />}
              {STATUS_LABELS[currentRun.status] || 'Updating'}
            </Badge>
          )}
          {isStreaming ? (
            <Badge variant="outline" className="run-time-badge"><Timer data-icon="inline-start" />{liveSeconds}s</Badge>
          ) : (executionDuration || currentRun?.execution_time_ms > 0) ? (
            <Badge variant="outline" className="run-time-badge">
              <Clock3 data-icon="inline-start" />{executionDuration || (currentRun.execution_time_ms / 1000).toFixed(2)}s
            </Badge>
          ) : null}
          {canCancel && (
            <Button type="button" variant="destructive" size="sm" onClick={onCancelRun} disabled={isCancelling}>
              <XCircle data-icon="inline-start" />{isCancelling ? 'Cancelling…' : 'Cancel workflow'}
            </Button>
          )}
        </div>
      </div>

      {!currentRun ? (
        <Card className="surface-card empty-state">
          <span className="empty-state__icon" aria-hidden="true"><Activity /></span>
          <CardTitle className="empty-state__title">No runs to display</CardTitle>
          <CardDescription className="empty-state__description">
            Once you approve and start a plan, task progress and results will appear here.
          </CardDescription>
        </Card>
      ) : (
        <div className="execution-tracker-grid run-view-grid">
          <Card className="surface-card run-card">
            <CardHeader>
              <CardTitle>Workflow steps</CardTitle>
              <CardDescription>
                {plan?.length ? `${finishedCount} / ${plan.length} steps finished` : 'No step details available.'}
              </CardDescription>
            </CardHeader>
            <CardContent className="run-task-list">
              {(!plan || plan.length === 0) ? (
                <p className="run-empty-hint">Steps will appear when the backend provides the execution plan.</p>
              ) : plan.map((task, index) => (
                <article key={task?.id || index} className="run-task-card">
                  <span className={`execution-task-number${task?.status === 'running' ? ' execution-task-number--running' : ''}`}>
                    {index + 1}
                  </span>
                  <div className="run-task-card__content">
                    <p className="execution-task-description">{task?.description || `Step ${index + 1}`}</p>
                    {task?.node && task.node !== 'worker' && <span className="run-task-agent">{task.node}</span>}
                  </div>
                  <TaskStatusBadge status={task?.status} />
                </article>
              ))}
            </CardContent>
          </Card>

          <Card className="surface-card run-card run-results-card">
            <CardHeader className="run-results-card__header">
              <div>
                <CardTitle>Step results</CardTitle>
                <CardDescription>Outputs saved from each agent.</CardDescription>
              </div>
              {currentRun.run_id && (
                <Button type="button" variant="outline" size="sm" onClick={onOpenResults}>
                  <Download data-icon="inline-start" />Open results
                </Button>
              )}
            </CardHeader>
            <CardContent className="run-results-list">
              {(!results || results.length === 0) ? (
                <div className="run-results-empty">
                  <FileText aria-hidden="true" />
                  <p>{isStreaming ? 'Results will appear as steps finish.' : 'No outputs have been saved for this run yet.'}</p>
                </div>
              ) : results.map((result, index) => (
                <article key={result?.task_id || index} className="run-output-card">
                  <header className="run-output-card__header">
                    <div>
                      <p className="run-output-card__step">Step {result?.task_id || index + 1}</p>
                      {result?.description && <p className="run-output-card__description">{result.description}</p>}
                    </div>
                    <TaskStatusBadge status={result?.status || 'done'} />
                  </header>
                  <div className="run-output-card__content">
                    <StructuredText text={resultText(result)} />
                  </div>
                </article>
              ))}
            </CardContent>
          </Card>
        </div>
      )}
    </section>
  );
}
