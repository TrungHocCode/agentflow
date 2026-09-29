import { useEffect, useState } from 'react';
import { Activity, Clock3, Download, FileText, Timer, XCircle } from 'lucide-react';
import StructuredText from './StructuredText';
import TaskStatusBadge from './TaskStatusBadge';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from './ui/card';
import { Spinner } from './ui/spinner';

const STATUS_LABELS = {
  pending: 'Chờ duyệt',
  created: 'Đã tạo',
  waiting_for_approval: 'Chờ duyệt',
  queued: 'Đang chờ',
  running: 'Đang chạy',
  completed: 'Hoàn tất',
  failed: 'Thất bại',
  cancelled: 'Đã hủy',
  interrupted: 'Bị gián đoạn',
  abandoned: 'Đã dừng'
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
          <p className="page-eyebrow">THEO DÕI NGHIÊN CỨU</p>
          <h1 id="runs-title" className="page-title">Tiến độ quy trình</h1>
          <p className="page-description">
            {currentRun ? STATUS_LABELS[currentRun.status] || 'Đang cập nhật trạng thái' : 'Chưa có lượt chạy nào được chọn.'}
          </p>
        </div>
        <div className="run-heading-actions">
          {currentRun && (
            <Badge variant={statusTone === 'failed' ? 'destructive' : 'secondary'} className={`run-status run-status--${statusTone}`}>
              {isStreaming && <Spinner data-icon="inline-start" />}
              {STATUS_LABELS[currentRun.status] || 'Đang cập nhật'}
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
              <XCircle data-icon="inline-start" />{isCancelling ? 'Đang hủy…' : 'Hủy quy trình'}
            </Button>
          )}
        </div>
      </div>

      {!currentRun ? (
        <Card className="surface-card empty-state">
          <span className="empty-state__icon" aria-hidden="true"><Activity /></span>
          <CardTitle className="empty-state__title">Chưa có lượt chạy để hiển thị</CardTitle>
          <CardDescription className="empty-state__description">
            Khi bạn duyệt và bắt đầu một kế hoạch, tiến độ từng bước và kết quả sẽ xuất hiện tại đây.
          </CardDescription>
        </Card>
      ) : (
        <div className="execution-tracker-grid run-view-grid">
          <Card className="surface-card run-card">
            <CardHeader>
              <CardTitle>Các bước thực hiện</CardTitle>
              <CardDescription>
                {plan?.length ? `${finishedCount} / ${plan.length} bước đã kết thúc` : 'Chưa có thông tin bước thực hiện.'}
              </CardDescription>
            </CardHeader>
            <CardContent className="run-task-list">
              {(!plan || plan.length === 0) ? (
                <p className="run-empty-hint">Các bước sẽ hiển thị khi backend gửi kế hoạch chạy.</p>
              ) : plan.map((task, index) => (
                <article key={task?.id || index} className="run-task-card">
                  <span className={`execution-task-number${task?.status === 'running' ? ' execution-task-number--running' : ''}`}>
                    {index + 1}
                  </span>
                  <div className="run-task-card__content">
                    <p className="execution-task-description">{task?.description || `Bước ${index + 1}`}</p>
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
                <CardTitle>Kết quả theo bước</CardTitle>
                <CardDescription>Đầu ra được lưu lại từ từng agent.</CardDescription>
              </div>
              {currentRun.run_id && (
                <Button type="button" variant="outline" size="sm" onClick={onOpenResults}>
                  <Download data-icon="inline-start" />Mở báo cáo
                </Button>
              )}
            </CardHeader>
            <CardContent className="run-results-list">
              {(!results || results.length === 0) ? (
                <div className="run-results-empty">
                  <FileText aria-hidden="true" />
                  <p>{isStreaming ? 'Kết quả sẽ xuất hiện khi các bước hoàn tất.' : 'Lượt chạy này chưa có đầu ra được lưu.'}</p>
                </div>
              ) : results.map((result, index) => (
                <article key={result?.task_id || index} className="run-output-card">
                  <header className="run-output-card__header">
                    <div>
                      <p className="run-output-card__step">Bước {result?.task_id || index + 1}</p>
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
