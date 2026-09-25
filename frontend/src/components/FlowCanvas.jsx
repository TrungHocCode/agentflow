import React from 'react';
import { GitMerge, ArrowDown, CheckCircle, Clock, AlertCircle } from 'lucide-react';

export default function FlowCanvas({ plan }) {
  if (!plan || plan.length === 0) {
    return (
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', padding: '2rem' }}>
        <GitMerge style={{ width: '48px', height: '48px', color: 'var(--text-muted)', marginBottom: '1rem' }} />
        <h3 style={{ fontSize: '1.125rem', color: 'var(--text-primary)', marginBottom: '0.5rem' }}>Chưa có sơ đồ quy trình</h3>
        <p style={{ fontSize: '0.875rem', color: 'var(--text-muted)' }}>Hãy bắt đầu bằng cách mô tả yêu cầu nghiên cứu trong phần Trò chuyện.</p>
      </div>
    );
  }

  return (
    <div style={{ flex: 1, padding: '1.5rem', display: 'flex', flexDirection: 'column', gap: '1.25rem', overflowY: 'auto' }}>
      <div className="glass-panel" style={{ padding: '1.25rem', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div>
          <h2 style={{ fontSize: '1.125rem', color: '#fff' }}>Sơ đồ quy trình</h2>
          <p style={{ fontSize: '0.8125rem', color: 'var(--text-muted)' }}>Theo dõi các bước và mối liên hệ giữa chúng.</p>
        </div>
        <span className="badge badge-done">{plan.length} bước</span>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '1.5rem', padding: '2rem 0' }}>
        {plan.map((task, idx) => {
          const isDone = task.status === 'done';
          const isRunning = task.status === 'running';
          const isPartial = task.status === 'partial';
          const isFailed = task.status === 'failed';
          const statusDetails = {
            done: { label: 'Hoàn tất', className: 'badge-done', Icon: CheckCircle },
            completed: { label: 'Hoàn tất', className: 'badge-done', Icon: CheckCircle },
            partial: { label: 'Hoàn tất một phần', className: 'badge-partial', Icon: AlertCircle },
            running: { label: 'Đang chạy', className: 'badge-running', Icon: Clock },
            failed: { label: 'Thất bại', className: 'badge-failed', Icon: AlertCircle },
            skipped: { label: 'Đã bỏ qua', className: 'badge-pending', Icon: Clock },
            pending: { label: 'Chờ đến lượt', className: 'badge-pending', Icon: Clock }
          }[task.status] || { label: 'Chờ đến lượt', className: 'badge-pending', Icon: Clock };
          const StatusIcon = statusDetails.Icon;

          return (
            <React.Fragment key={task.id}>
              {idx > 0 && (
                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '0.25rem' }}>
                  <ArrowDown style={{ width: '20px', height: '20px', color: 'var(--accent-indigo)' }} />
                  <span style={{ fontSize: '0.6875rem', color: 'var(--text-muted)', background: 'rgba(30,41,59,0.5)', padding: '0.125rem 0.5rem', borderRadius: '4px' }}>
                    {task.dependencies?.length
                      ? `Thực hiện sau bước ${task.dependencies.join(', ')}`
                      : 'Có thể bắt đầu độc lập'}
                  </span>
                </div>
              )}

              <div 
                className="glass-panel"
                style={{ 
                  width: '100%', 
                  maxWidth: '520px', 
                  padding: '1.25rem',
                  border: isRunning
                    ? '1px solid var(--accent-cyan)'
                    : isDone
                      ? '1px solid rgba(16,185,129,0.4)'
                      : isPartial
                        ? '1px solid rgba(197,150,91,0.55)'
                        : isFailed
                          ? '1px solid rgba(189,110,98,0.45)'
                          : '1px solid var(--glass-border)',
                  boxShadow: isRunning ? '0 0 25px rgba(6,182,212,0.3)' : 'none'
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '0.75rem' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.625rem' }}>
                    <span style={{ width: '28px', height: '28px', borderRadius: '8px', background: 'var(--gradient-brand)', color: '#fff', fontSize: '0.875rem', fontWeight: '700', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                      {task.id}
                    </span>
                    <span style={{ fontSize: '0.9375rem', fontWeight: '700', color: '#fff' }}>Bước {idx + 1}</span>
                  </div>

                  <span className={`badge ${statusDetails.className}`}>
                    <StatusIcon style={{ width: '12px', height: '12px' }} />
                    {statusDetails.label}
                  </span>
                </div>

                <p style={{ fontSize: '0.875rem', color: 'var(--text-secondary)', marginBottom: '0.75rem', lineHeight: 1.4 }}>
                  {task.description}
                </p>

              </div>
            </React.Fragment>
          );
        })}
      </div>
    </div>
  );
}
