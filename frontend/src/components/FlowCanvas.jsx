import React from 'react';
import { GitMerge, ArrowDown, CheckCircle, Clock, Cpu, FileText } from 'lucide-react';

export default function FlowCanvas({ plan }) {
  if (!plan || plan.length === 0) {
    return (
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', padding: '2rem' }}>
        <GitMerge style={{ width: '48px', height: '48px', color: 'var(--text-muted)', marginBottom: '1rem' }} />
        <h3 style={{ fontSize: '1.125rem', color: 'var(--text-primary)', marginBottom: '0.5rem' }}>Chưa Có Sơ Đồ Execution Flow</h3>
        <p style={{ fontSize: '0.875rem', color: 'var(--text-muted)' }}>Hãy vào phần Conversational Studio để trao đổi với Supervisor Agent tạo Kế hoạch DAG.</p>
      </div>
    );
  }

  return (
    <div style={{ flex: 1, padding: '1.5rem', display: 'flex', flexDirection: 'column', gap: '1.25rem', overflowY: 'auto' }}>
      <div className="glass-panel" style={{ padding: '1.25rem', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div>
          <h2 style={{ fontSize: '1.125rem', color: '#fff' }}>Sơ Đồ Thực Thi Đồ Thị DAG (Flow Canvas)</h2>
          <p style={{ fontSize: '0.8125rem', color: 'var(--text-muted)' }}>Mô hình thực thi phụ thuộc theo thứ tự giữa các Worker Agents</p>
        </div>
        <span className="badge badge-done">{plan.length} Tasks Defined</span>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '1.5rem', padding: '2rem 0' }}>
        {plan.map((task, idx) => {
          const isDone = task.status === 'done';
          const isRunning = task.status === 'running';

          return (
            <React.Fragment key={task.id}>
              {idx > 0 && (
                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '0.25rem' }}>
                  <ArrowDown style={{ width: '20px', height: '20px', color: 'var(--accent-indigo)' }} />
                  <span style={{ fontSize: '0.6875rem', color: 'var(--text-muted)', background: 'rgba(30,41,59,0.5)', padding: '0.125rem 0.5rem', borderRadius: '4px' }}>
                    Phụ thuộc Task {task.dependencies.join(', ') || idx}
                  </span>
                </div>
              )}

              <div 
                className="glass-panel"
                style={{ 
                  width: '100%', 
                  maxWidth: '520px', 
                  padding: '1.25rem',
                  border: isRunning ? '1px solid var(--accent-cyan)' : (isDone ? '1px solid rgba(16,185,129,0.4)' : '1px solid var(--glass-border)'),
                  boxShadow: isRunning ? '0 0 25px rgba(6,182,212,0.3)' : 'none'
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '0.75rem' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.625rem' }}>
                    <span style={{ width: '28px', height: '28px', borderRadius: '8px', background: 'var(--gradient-brand)', color: '#fff', fontSize: '0.875rem', fontWeight: '700', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                      {task.id}
                    </span>
                    <span style={{ fontSize: '0.9375rem', fontWeight: '700', color: '#fff' }}>Node: {task.node}</span>
                  </div>

                  <span className={`badge ${isDone ? 'badge-done' : (isRunning ? 'badge-running' : 'badge-pending')}`}>
                    {isDone ? <CheckCircle style={{ width: '12px', height: '12px' }} /> : <Clock style={{ width: '12px', height: '12px' }} />}
                    {task.status}
                  </span>
                </div>

                <p style={{ fontSize: '0.875rem', color: 'var(--text-secondary)', marginBottom: '0.75rem', lineHeight: 1.4 }}>
                  {task.description}
                </p>

                <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', fontSize: '0.75rem', color: 'var(--text-muted)', paddingTop: '0.5rem', borderTop: '1px solid rgba(255,255,255,0.05)' }}>
                  <span style={{ display: 'flex', alignItems: 'center', gap: '0.25rem' }}>
                    <Cpu style={{ width: '14px', height: '14px', color: 'var(--accent-violet)' }} /> ReAct Worker Agent
                  </span>
                  <span style={{ display: 'flex', alignItems: 'center', gap: '0.25rem' }}>
                    <FileText style={{ width: '14px', height: '14px', color: 'var(--accent-cyan)' }} /> Tool Whitelist Attached
                  </span>
                </div>
              </div>
            </React.Fragment>
          );
        })}
      </div>
    </div>
  );
}
