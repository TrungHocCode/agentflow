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
          <p className="page-eyebrow">CẤU TRÚC THỰC THI</p>
          <h1 id="flow-title" className="page-title">Sơ đồ quy trình</h1>
          <p className="page-description">Xem cách các bước phụ thuộc nhau và bước nào có thể thực hiện song song.</p>
        </div>
        {plan?.length > 0 && <Badge variant="secondary">{plan.length} bước</Badge>}
      </div>

      {!plan?.length ? (
        <Card className="surface-card empty-state">
          <span className="empty-state__icon" aria-hidden="true"><GitMerge /></span>
          <CardTitle className="empty-state__title">Chưa có sơ đồ quy trình</CardTitle>
          <CardDescription className="empty-state__description">
            Hãy bắt đầu bằng cách mô tả yêu cầu nghiên cứu trong cuộc trò chuyện. Kế hoạch sau đó sẽ xuất hiện ở đây.
          </CardDescription>
        </Card>
      ) : (
        <Card className="surface-card">
          <CardHeader>
            <div className="flex items-center gap-2">
              <Sparkles className="text-primary" />
              <CardTitle>{runStatus ? 'Tiến độ các bước' : 'Kế hoạch hiện tại'}</CardTitle>
            </div>
            <CardDescription>
              Các node trên cùng một giai đoạn không phụ thuộc lẫn nhau và có thể chạy song song.
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
