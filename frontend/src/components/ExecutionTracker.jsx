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
  partial: 'Hoàn tất một phần',
  completed: 'Hoàn tất',
  failed: 'Thất bại',
  skipped: 'Đã bỏ qua'
};

const formatMilliseconds = value => {
  const milliseconds = Number(value) || 0;
  return milliseconds >= 1000
    ? `${(milliseconds / 1000).toFixed(2)}s`
    : `${milliseconds.toFixed(0)}ms`;
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
  const executionMetrics = currentRun?.metadata?.execution_metrics;
  const executionTimings = currentRun?.metadata?.execution_timings || [];
  const timedComponents = [
    ...Object.entries(executionMetrics?.llm?.by_agent || {}).map(([name, stats]) => ({
      key: `llm-${name}`,
      type: 'LLM',
      name,
      stats
    })),
    ...Object.entries(executionMetrics?.tools?.by_tool || {}).map(([name, stats]) => ({
      key: `tool-${name}`,
      type: 'Tool',
      name,
      stats
    }))
  ].sort((left, right) => right.stats.total_ms - left.stats.total_ms);

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
                    <span className={`badge ${['done', 'completed'].includes(t?.status) ? 'badge-done' : (t?.status === 'partial' ? 'badge-partial' : (t?.status === 'failed' ? 'badge-failed' : (t?.status === 'running' ? 'badge-running' : 'badge-pending')))}`}>
                      {TASK_STATUS_LABELS[t?.status] || 'Chờ đến lượt'}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>

          {executionMetrics && (
            <div className="glass-panel" style={{ padding: '1.25rem' }}>
              <h3 style={{ fontSize: '0.9375rem', color: '#fff', marginBottom: '0.875rem' }}>
                Phân tích thời gian
              </h3>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.625rem', marginBottom: '1rem' }}>
                {[
                  { label: 'LLM', stats: executionMetrics.llm },
                  { label: 'Tools', stats: executionMetrics.tools }
                ].map(({ label, stats }) => (
                  <div key={label} className="glass-card" style={{ padding: '0.75rem' }}>
                    <div style={{ color: 'var(--text-muted)', fontSize: '0.75rem' }}>{label}</div>
                    <div style={{ color: '#fff', fontSize: '1rem', fontWeight: 700, marginTop: '0.25rem' }}>
                      {formatMilliseconds(stats?.total_ms)}
                    </div>
                    <div style={{ color: 'var(--text-muted)', fontSize: '0.7rem', marginTop: '0.25rem' }}>
                      {stats?.call_count || 0} lần gọi · chậm nhất {formatMilliseconds(stats?.max_ms)}
                    </div>
                  </div>
                ))}
              </div>

              {timedComponents.length > 0 && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
                  <div style={{ color: 'var(--text-muted)', fontSize: '0.75rem' }}>Theo agent / tool</div>
                  {timedComponents.map(({ key, type, name, stats }) => (
                    <div key={key} style={{ display: 'flex', justifyContent: 'space-between', gap: '0.75rem', fontSize: '0.75rem' }}>
                      <span style={{ color: 'var(--text-primary)', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                        {type} · {name} ({stats.call_count} lần)
                      </span>
                      <span style={{ color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>
                        {formatMilliseconds(stats.total_ms)}
                      </span>
                    </div>
                  ))}
                </div>
              )}

              {executionMetrics.execute_wall_ms != null && (
                <div style={{ color: 'var(--text-muted)', fontSize: '0.7rem', marginTop: '0.75rem' }}>
                  Tổng thời gian chạy {formatMilliseconds(executionMetrics.execute_wall_ms)}; phần setup/điều phối/chưa quy được vào LLM hoặc tool: {formatMilliseconds(executionMetrics.unattributed_execute_ms)}.
                </div>
              )}

              {executionTimings.length > 0 && (
                <details style={{ marginTop: '0.875rem' }}>
                  <summary style={{ color: 'var(--text-muted)', fontSize: '0.75rem', cursor: 'pointer' }}>
                    Chi tiết {executionTimings.length} lần gọi
                  </summary>
                  <div style={{ maxHeight: '220px', overflowY: 'auto', marginTop: '0.5rem' }}>
                    {[...executionTimings]
                      .sort((left, right) => new Date(left.started_at) - new Date(right.started_at))
                      .map(timing => (
                        <div key={timing.span_id} style={{ display: 'flex', justifyContent: 'space-between', gap: '0.75rem', padding: '0.35rem 0', borderBottom: '1px solid rgba(255,255,255,0.06)', fontSize: '0.7rem' }}>
                          <span style={{ color: 'var(--text-primary)' }}>
                            {timing.operation === 'llm' ? 'LLM' : 'Tool'} · {timing.name}
                            {timing.model ? ` · ${timing.model}` : ''}
                            {timing.task_id != null ? ` · bước ${timing.task_id}` : ''}
                            {timing.iteration ? ` · vòng ${timing.iteration}` : ''}
                          </span>
                          <span style={{ color: timing.status === 'failed' || timing.result_status === 'failed' ? 'var(--accent-rose)' : 'var(--text-muted)', whiteSpace: 'nowrap' }}>
                            {formatMilliseconds(timing.duration_ms)}{timing.status === 'failed' || timing.result_status === 'failed' ? ' · lỗi' : timing.result_status === 'partial' ? ' · một phần' : ''}
                          </span>
                        </div>
                      ))}
                  </div>
                </details>
              )}
            </div>
          )}
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
                    <span className={`badge ${res?.status === 'failed' ? 'badge-failed' : (res?.status === 'partial' ? 'badge-partial' : 'badge-done')}`}>
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
