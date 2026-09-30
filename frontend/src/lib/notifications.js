import { toast } from '../components/ui/toast';

export function notify({ id, title, description, type = 'info', actionProps }) {
  return toast.add({ id, title, description, type, actionProps,
    timeout: type === 'error' ? 10000 : 5000,
    priority: type === 'error' ? 'high' : 'low' });
}

export function notifyRunFinished(runId, status, onViewDetails) {
  const messages = {
    completed: ['Research complete', 'Your results are ready. Open the report to review and download files.', 'success'],
    failed: ['Research could not finish', 'Open run details to see the failed step and error information.', 'error'],
    interrupted: ['Research interrupted', 'Open run details to review the latest saved progress.', 'warning'],
    cancelled: ['Workflow cancelled', 'The workflow has stopped. Saved outputs remain in Run history.', 'info'],
    abandoned: ['Workflow stopped', 'Open run details to review the latest saved progress.', 'warning']
  };
  if (!messages[status]) return;
  const [title, description, type] = messages[status];
  notify({ id: `run:${runId}:${status}`, title, description, type,
    actionProps: { children: status === 'completed' ? 'View results' : 'View details', onClick: onViewDetails } });
}
