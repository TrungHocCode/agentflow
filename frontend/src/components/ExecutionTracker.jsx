import React, { useEffect, useState } from 'react';
import { Clock, Activity, FileText, Timer, Download, XCircle } from 'lucide-react';
import StructuredText from './StructuredText';

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

const TASK_STATUS_LABELS = {
  pending: 'Chờ đến lượt',
  queued: 'Đang chờ',
  running: 'Đang chạy',
  done: 'Hoàn tất',
  completed: 'Hoàn tất',
  failed: 'Thất bại',
  skipped: 'Đã bỏ qua'
};

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

  useEffect(() => {
    let timer;
    if (isStreaming) {
      setLiveSeconds(0);
      const start = Date.now();
      timer = setInterval(() => {
        setLiveSeconds(((Date.now() - start) / 1000).toFixed(1));
      }, 100);
    }
    return () => clearInterval(timer);
  }, [isStreaming]);

  const canCancel = ['queued', 'running'].includes(currentRun?.status);

  return (
    <div style={{ flex: 1, padding: '1.5rem', display: 'flex', flexDirection: 'column', gap: '1.25rem', overflowY: 'auto' }}>
      {/* Header Banner */}
      <div className="glass-panel" style={{ padding: '1.25rem', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
          <div style={{ padding: '0.5rem', borderRadius: '8px', background: isStreaming ? 'rgba(6,182,212,0.15)' : 'rgba(16,185,129,0.15)', border: isStreaming ? '1px solid rgba(6,182,212,0.3)' : '1px solid rgba(16,185,129,0.3)' }}>
            <Activity style={{ width: '22px', height: '22px', color: isStreaming ? 'var(--accent-cyan)' : 'var(--accent-emerald)' }} />
          </div>
          <div>
            <h2 style={{ fontSize: '1.125rem', color: '#fff' }}>Tiến độ quy trình</h2>
            <p style={{ fontSize: '0.8125rem', color: 'var(--text-muted)' }}>
              {currentRun ? STATUS_LABELS[currentRun.status] || 'Đang cập nhật' : 'Chưa có lượt chạy'}
            </p>
          </div>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
          {isStreaming ? (
            <span style={{ display: 'flex', alignItems: 'center', gap: '5px', padding: '0.375rem 0.75rem', borderRadius: '20px', background: 'rgba(6,182,212,0.15)', border: '1px solid rgba(6,182,212,0.4)', color: 'var(--accent-cyan)', fontSize: '0.8125rem', fontWeight: '600' }}>
              <Timer style={{ width: '14px', height: '14px' }} className="spin-slow" />
              Đang chạy: {liveSeconds}s
            </span>
          ) : (
            (executionDuration || currentRun?.execution_time_ms > 0) && (
              <span style={{ display: 'flex', alignItems: 'center', gap: '5px', padding: '0.375rem 0.75rem', borderRadius: '20px', background: 'rgba(16,185,129,0.15)', border: '1px solid rgba(16,185,129,0.4)', color: 'var(--accent-emerald)', fontSize: '0.8125rem', fontWeight: '600' }}>
                <Clock style={{ width: '14px', height: '14px' }} />
                Thời gian: {executionDuration || (currentRun.execution_time_ms / 1000).toFixed(2)}s
              </span>
            )
          )}

          {canCancel && (
            <button
              className="btn-secondary"
              onClick={onCancelRun}
              disabled={isCancelling}
              style={{ color: 'var(--accent-rose)', gap: '0.4rem' }}
            >
              <XCircle size={15} /> {isCancelling ? 'Đang hủy…' : 'Hủy quy trình'}
            </button>
          )}
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1.25rem', flex: 1 }}>
        {/* Workflow steps */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          <div className="glass-panel" style={{ padding: '1.25rem' }}>
            <h3 style={{ fontSize: '0.9375rem', color: '#fff', marginBottom: '0.875rem' }}>Các bước</h3>
            {(!plan || plan.length === 0) ? (
              <p style={{ fontSize: '0.8125rem', color: 'var(--text-muted)' }}>Chưa có bước nào trong quy trình.</p>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                {plan.map((t, idx) => (
                  <div key={t?.id || idx} className="glass-card" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '0.75rem', padding: '0.75rem 0.875rem' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', minWidth: 0 }}>
                      <span style={{ width: '28px', height: '28px', borderRadius: '50%', display: 'grid', placeItems: 'center', flexShrink: 0, color: '#fff', background: t?.status === 'running' ? 'var(--accent-cyan)' : 'rgba(99,102,241,0.35)', fontSize: '0.8rem', fontWeight: '700' }}>{idx + 1}</span>
                      <span style={{ fontSize: '0.8125rem', color: 'var(--text-primary)' }}>{t?.description || `Bước ${idx + 1}`}</span>
                    </div>
                    <span className={`badge ${['done', 'completed'].includes(t?.status) ? 'badge-done' : (t?.status === 'running' ? 'badge-running' : 'badge-pending')}`}>
                      {TASK_STATUS_LABELS[t?.status] || 'Chờ đến lượt'}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Right Column: Execution Output Artifacts Viewer */}
        <div className="glass-panel" style={{ padding: '1.25rem', display: 'flex', flexDirection: 'column', gap: '1rem', overflowY: 'auto' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '0.75rem' }}>
            <FileText style={{ width: '20px', height: '20px', color: 'var(--accent-violet)' }} />
            <h3 style={{ flex: 1, fontSize: '1rem', color: '#fff' }}>Kết quả theo bước</h3>
            {currentRun?.run_id && (
              <button className="btn-secondary" onClick={onOpenResults} style={{ gap: '0.4rem' }}>
                <Download size={15} /> Mở báo cáo / tải kết quả
              </button>
            )}
          </div>

          {(!results || results.length === 0) ? (
            <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', textAlign: 'center', color: 'var(--text-muted)' }}>
              <FileText style={{ width: '40px', height: '40px', marginBottom: '0.75rem', opacity: 0.5 }} />
              <p style={{ fontSize: '0.875rem' }}>Kết quả sẽ xuất hiện tại đây khi các bước hoàn tất.</p>
            </div>
          ) : (
            results.map((res, i) => {
              const resultText = typeof res?.result === 'object'
                ? JSON.stringify(res.result, null, 2)
                : String(res?.result ?? (typeof res === 'object' ? JSON.stringify(res, null, 2) : String(res)));

              return (
                <div key={i} className="glass-card" style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                    <span style={{ fontSize: '0.875rem', fontWeight: '700', color: 'var(--accent-cyan)' }}>
                      Bước {res?.task_id || i + 1}
                    </span>
                    <span className={`badge ${res?.status === 'failed' ? 'badge-failed' : 'badge-done'}`}>
                      {TASK_STATUS_LABELS[res?.status] || 'Hoàn tất'}
                    </span>
                  </div>
                  <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>{res?.description || ''}</p>
                  <div style={{ background: 'rgba(15,23,42,0.45)', padding: '0.75rem', borderRadius: '6px', fontSize: '0.8125rem', color: '#e2e8f0', maxHeight: '300px', overflowY: 'auto' }}>
                    <StructuredText text={resultText} />
                  </div>
                </div>
              );
            })
          )}
        </div>

      </div>
    </div>
  );
}
