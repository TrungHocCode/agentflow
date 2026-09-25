import { AlertCircle, CheckCircle2, Circle, LoaderCircle, SkipForward } from 'lucide-react';

const TASK_STATUS = {
  pending: { label: 'Chờ đến lượt', tone: 'pending', Icon: Circle },
  queued: { label: 'Đang chờ', tone: 'pending', Icon: Circle },
  running: { label: 'Đang chạy', tone: 'running', Icon: LoaderCircle },
  done: { label: 'Hoàn tất', tone: 'done', Icon: CheckCircle2 },
  success: { label: 'Hoàn tất', tone: 'done', Icon: CheckCircle2 },
  completed: { label: 'Hoàn tất', tone: 'done', Icon: CheckCircle2 },
  partial: { label: 'Hoàn tất một phần', tone: 'partial', Icon: AlertCircle },
  failed: { label: 'Thất bại', tone: 'failed', Icon: AlertCircle },
  skipped: { label: 'Đã bỏ qua', tone: 'skipped', Icon: SkipForward },
  cancelled: { label: 'Đã hủy', tone: 'skipped', Icon: SkipForward },
  interrupted: { label: 'Bị gián đoạn', tone: 'partial', Icon: AlertCircle }
};

export default function TaskStatusBadge({ status }) {
  const normalizedStatus = String(status || 'pending').toLowerCase();
  const details = TASK_STATUS[normalizedStatus] || TASK_STATUS.pending;
  const Icon = details.Icon;

  return (
    <span
      className={`task-status task-status--${details.tone}`}
      aria-label={`Trạng thái: ${details.label}`}
      title={details.label}
    >
      <Icon
        aria-hidden="true"
        className={normalizedStatus === 'running' ? 'task-status__spinner' : undefined}
        size={14}
      />
      <span>{details.label}</span>
    </span>
  );
}
