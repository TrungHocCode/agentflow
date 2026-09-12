import React, { useRef, useEffect, useState } from 'react';
import { Terminal, CheckCircle2, Clock, Activity, FileText, AlertTriangle, RefreshCw, Timer } from 'lucide-react';

export default function ExecutionTracker({ currentRun, logs, results, plan, isStreaming, executionDuration }) {
  const terminalEndRef = useRef(null);
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

  useEffect(() => {
    if (terminalEndRef.current) {
      terminalEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [logs]);

  return (
    <div style={{ flex: 1, padding: '1.5rem', display: 'flex', flexDirection: 'column', gap: '1.25rem', overflowY: 'auto' }}>
      {/* Header Banner */}
      <div className="glass-panel" style={{ padding: '1.25rem', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
          <div style={{ padding: '0.5rem', borderRadius: '8px', background: isStreaming ? 'rgba(6,182,212,0.15)' : 'rgba(16,185,129,0.15)', border: isStreaming ? '1px solid rgba(6,182,212,0.3)' : '1px solid rgba(16,185,129,0.3)' }}>
            <Activity style={{ width: '22px', height: '22px', color: isStreaming ? 'var(--accent-cyan)' : 'var(--accent-emerald)' }} />
          </div>
          <div>
            <h2 style={{ fontSize: '1.125rem', color: '#fff' }}>Realtime Execution Tracker & Log Stream</h2>
            <p style={{ fontSize: '0.8125rem', color: 'var(--text-muted)' }}>
              Run ID: <code style={{ color: 'var(--accent-cyan)' }}>{currentRun ? currentRun.run_id : 'No active run'}</code>
            </p>
          </div>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
          {/* Realtime Execution Timer Badge */}
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

          <span className={`badge ${isStreaming ? 'badge-running' : 'badge-done'}`}>
            {isStreaming ? (
              <>
                <RefreshCw style={{ width: '12px', height: '12px' }} className="spin-slow" /> Streaming SSE Events...
              </>
            ) : (
              <>
                <CheckCircle2 style={{ width: '12px', height: '12px' }} /> Stream Ready
              </>
            )}
          </span>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1.25rem', flex: 1 }}>
        {/* Left Column: Task Stepper + Realtime Log Terminal */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          {/* Task Progress Stepper */}
          <div className="glass-panel" style={{ padding: '1.25rem' }}>
            <h3 style={{ fontSize: '0.9375rem', color: '#fff', marginBottom: '0.875rem' }}>Trạng Thái Tiến Độ Tasks</h3>
            {(!plan || plan.length === 0) ? (
              <p style={{ fontSize: '0.8125rem', color: 'var(--text-muted)' }}>Chưa có task nào được tạo.</p>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                {plan.map((t, idx) => (
                  <div key={t?.id || idx} className="glass-card" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0.625rem 0.875rem' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.625rem' }}>
                      <span style={{ fontSize: '0.8125rem', fontWeight: '700', color: '#fff' }}>Task {t?.id || idx + 1}</span>
                      <span style={{ fontSize: '0.8125rem', color: 'var(--text-secondary)' }}>({t?.node || 'Worker'})</span>
                    </div>
                    <span className={`badge ${t?.status === 'done' ? 'badge-done' : (t?.status === 'running' ? 'badge-running' : 'badge-pending')}`}>
                      {t?.status || 'pending'}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Realtime Terminal Window */}
          <div className="glass-panel" style={{ flex: 1, padding: '1rem', display: 'flex', flexDirection: 'column', background: '#050811', border: '1px solid rgba(255,255,255,0.1)' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.75rem', paddingBottom: '0.5rem', borderBottom: '1px solid rgba(255,255,255,0.1)' }}>
              <Terminal style={{ width: '16px', height: '16px', color: 'var(--accent-emerald)' }} />
              <span style={{ fontSize: '0.8125rem', fontFamily: 'var(--font-mono)', color: 'var(--accent-emerald)', fontWeight: '600' }}>
                agentflow@server:~/logs $
              </span>
            </div>

            <div style={{ flex: 1, fontFamily: 'var(--font-mono)', fontSize: '0.75rem', color: '#cbd5e1', overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '0.375rem' }}>
              {(!logs || logs.length === 0) ? (
                <p style={{ color: 'var(--text-muted)' }}>Chưa có log sự kiện streaming nào...</p>
              ) : (
                logs.map((log, i) => (
                  <div key={i} style={{ lineHeight: 1.4, wordBreak: 'break-all' }}>
                    <span style={{ color: 'var(--accent-cyan)' }}>[{new Date().toLocaleTimeString()}]</span> {typeof log === 'string' ? log : JSON.stringify(log, null, 2)}
                  </div>
                ))
              )}
              <div ref={terminalEndRef} />
            </div>
          </div>
        </div>

        {/* Right Column: Execution Output Artifacts Viewer */}
        <div className="glass-panel" style={{ padding: '1.25rem', display: 'flex', flexDirection: 'column', gap: '1rem', overflowY: 'auto' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <FileText style={{ width: '20px', height: '20px', color: 'var(--accent-violet)' }} />
            <h3 style={{ fontSize: '1rem', color: '#fff' }}>Output Artifacts & Reports Result</h3>
          </div>

          {(!results || results.length === 0) ? (
            <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', textAlign: 'center', color: 'var(--text-muted)' }}>
              <FileText style={{ width: '40px', height: '40px', marginBottom: '0.75rem', opacity: 0.5 }} />
              <p style={{ fontSize: '0.875rem' }}>Kết quả thực thi sẽ hiển thị ở đây sau khi Worker Nodes hoàn thành.</p>
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
                      Task {res?.task_id || i + 1} [{res?.node || 'Worker'}]
                    </span>
                    <span className="badge badge-done">{res?.status || 'done'}</span>
                  </div>
                  <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>{res?.description || ''}</p>
                  <div style={{ background: 'rgba(15,23,42,0.8)', padding: '0.75rem', borderRadius: '6px', fontSize: '0.8125rem', color: '#e2e8f0', fontFamily: 'var(--font-mono)', whiteSpace: 'pre-wrap', maxHeight: '300px', overflowY: 'auto' }}>
                    {resultText}
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
