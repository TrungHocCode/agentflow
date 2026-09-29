import { AlertCircle, CheckCircle2, Circle, SkipForward } from 'lucide-react';
import { Badge } from './ui/badge';
import { Spinner } from './ui/spinner';

const TASK_STATUS = {
  pending: { label: 'Pending', tone: 'pending', variant: 'outline', Icon: Circle },
  queued: { label: 'Queued', tone: 'pending', variant: 'outline', Icon: Circle },
  running: { label: 'Running', tone: 'running', variant: 'secondary', Icon: Spinner },
  done: { label: 'Completed', tone: 'done', variant: 'secondary', Icon: CheckCircle2 },
  success: { label: 'Completed', tone: 'done', variant: 'secondary', Icon: CheckCircle2 },
  completed: { label: 'Completed', tone: 'done', variant: 'secondary', Icon: CheckCircle2 },
  partial: { label: 'Partially complete', tone: 'partial', variant: 'outline', Icon: AlertCircle },
  failed: { label: 'Failed', tone: 'failed', variant: 'destructive', Icon: AlertCircle },
  skipped: { label: 'Skipped', tone: 'skipped', variant: 'ghost', Icon: SkipForward },
  cancelled: { label: 'Cancelled', tone: 'skipped', variant: 'ghost', Icon: SkipForward },
  interrupted: { label: 'Interrupted', tone: 'partial', variant: 'outline', Icon: AlertCircle }
};

export default function TaskStatusBadge({ status }) {
  const normalizedStatus = String(status || 'pending').toLowerCase();
  const details = TASK_STATUS[normalizedStatus] || TASK_STATUS.pending;
  const Icon = details.Icon;

  return (
    <Badge
      variant={details.variant}
      className={`task-status task-status--${details.tone}`}
      aria-label={`Status: ${details.label}`}
      title={details.label}
    >
      <Icon data-icon="inline-start" />
      {details.label}
    </Badge>
  );
}
