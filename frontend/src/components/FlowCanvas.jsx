import { GitMerge, Sparkles } from 'lucide-react';
import WorkflowProgressGraph from './WorkflowProgressGraph';
import { Badge } from './ui/badge';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from './ui/card';

const TERMINAL_STATUSES = new Set(['done', 'success', 'completed', 'partial', 'failed', 'skipped', 'cancelled', 'interrupted']);

export default function FlowCanvas({ plan }) {
  const hasExecution = plan?.some(task => task?.status && task.status !== 'pending');
  const allFinished = plan?.length > 0 && plan.every(task => TERMINAL_STATUSES.has(String(task?.status || '').toLowerCase()));
  const hasFailure = plan?.some(task => ['failed', 'interrupted'].includes(String(task?.status || '').toLowerCase()));
  const runStatus = hasExecution ? (hasFailure ? 'failed' : allFinished ? 'completed' : 'running') : undefined;

  return (
    <section className="page-frame" aria-labelledby="flow-title">
      <div className="page-heading">
        <div className="page-heading__copy">
          <p className="page-eyebrow">EXECUTION STRUCTURE</p>
          <h1 id="flow-title" className="page-title">Workflow diagram</h1>
          <p className="page-description">See step dependencies and which tasks can run in parallel.</p>
        </div>
        {plan?.length > 0 && <Badge variant="secondary">{plan.length} steps</Badge>}
      </div>

      {!plan?.length ? (
        <Card className="surface-card empty-state">
          <span className="empty-state__icon" aria-hidden="true"><GitMerge /></span>
          <CardTitle className="empty-state__title">No workflow diagram yet</CardTitle>
          <CardDescription className="empty-state__description">
            Describe your research request in the chat to create a plan. It will appear here when ready.
          </CardDescription>
        </Card>
      ) : (
        <Card className="surface-card">
          <CardHeader>
            <div className="flex items-center gap-2">
              <Sparkles className="text-primary" />
              <CardTitle>{runStatus ? 'Step progress' : 'Current plan'}</CardTitle>
            </div>
            <CardDescription>
              Steps in the same stage have no dependencies on each other and can run in parallel.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <WorkflowProgressGraph tasks={plan} runStatus={runStatus} />
          </CardContent>
        </Card>
      )}
    </section>
  );
}
