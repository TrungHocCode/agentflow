import { AlertCircle, CheckCircle2, Circle, SkipForward } from 'lucide-react';
import { Badge } from './ui/badge';
import { Spinner } from './ui/spinner';

const TASK_STATUS = {
  pending: { label: 'Chờ đến lượt', tone: 'pending', variant: 'outline', Icon: Circle },
  queued: { label: 'Đang chờ', tone: 'pending', variant: 'outline', Icon: Circle },
  running: { label: 'Đang chạy', tone: 'running', variant: 'secondary', Icon: Spinner },
  done: { label: 'Hoàn tất', tone: 'done', variant: 'secondary', Icon: CheckCircle2 },
  success: { label: 'Hoàn tất', tone: 'done', variant: 'secondary', Icon: CheckCircle2 },
  completed: { label: 'Hoàn tất', tone: 'done', variant: 'secondary', Icon: CheckCircle2 },
  partial: { label: 'Hoàn tất một phần', tone: 'partial', variant: 'outline', Icon: AlertCircle },
  failed: { label: 'Thất bại', tone: 'failed', variant: 'destructive', Icon: AlertCircle },
  skipped: { label: 'Đã bỏ qua', tone: 'skipped', variant: 'ghost', Icon: SkipForward },
  cancelled: { label: 'Đã hủy', tone: 'skipped', variant: 'ghost', Icon: SkipForward },
  interrupted: { label: 'Bị gián đoạn', tone: 'partial', variant: 'outline', Icon: AlertCircle }
};

export default function TaskStatusBadge({ status }) {
  const normalizedStatus = String(status || 'pending').toLowerCase();
  const details = TASK_STATUS[normalizedStatus] || TASK_STATUS.pending;
  const Icon = details.Icon;

  return (
    <Badge
      variant={details.variant}
      className={`task-status task-status--${details.tone}`}
      aria-label={`Trạng thái: ${details.label}`}
      title={details.label}
    >
      <Icon data-icon="inline-start" />
      {details.label}
    </Badge>
  );
}
