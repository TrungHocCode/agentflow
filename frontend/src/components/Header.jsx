import React from 'react';
import { Cpu, Activity, Sparkles, Server, LogOut } from 'lucide-react';

export default function Header({ selectedModel, setSelectedModel, backendStatus, user, onLogout }) {
  const models = [
    { id: 'qwen3:8b', name: 'Qwen3 8B (Recommended)' },
    { id: 'llama3:8b', name: 'Llama3 8B' },
    { id: 'gemma2:latest', name: 'Gemma2 9B' },
    { id: 'qwen3:0.6b', name: 'Qwen3 0.6B (Fast)' }
  ];

  return (
    <header className="glass-panel" style={{ borderRadius: 0, borderTop: 0, borderLeft: 0, borderRight: 0, padding: '0.875rem 1.5rem', display: 'flex', alignItems: 'center', justifyContent: 'space-between', zIndex: 50 }}>
      {/* Brand Logo */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
        <div style={{ width: '36px', height: '36px', borderRadius: '10px', background: 'var(--gradient-brand)', display: 'flex', alignItems: 'center', justifyContent: 'center', boxShadow: '0 0 15px rgba(99,102,241,0.5)' }}>
          <Sparkles style={{ width: '20px', height: '20px', color: '#fff' }} />
        </div>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <span style={{ fontSize: '1.25rem', fontWeight: '800', fontFamily: 'var(--font-display)', letterSpacing: '-0.02em' }} className="text-gradient">
              AgentFlow
            </span>
            <span className="badge" style={{ background: 'rgba(99,102,241,0.15)', color: '#a5b4fc', border: '1px solid rgba(99,102,241,0.3)' }}>
              v1.0 Core
            </span>
          </div>
          <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>LLM Supervisor-Worker Agent Platform</p>
        </div>
      </div>

      {/* Right Controls: Model Selector + Backend Status */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '1.25rem' }}>
        {/* Model Dropdown */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', background: 'rgba(30,41,59,0.6)', padding: '0.375rem 0.75rem', borderRadius: 'var(--radius-md)', border: '1px solid var(--glass-border)' }}>
          <Cpu style={{ width: '16px', height: '16px', color: 'var(--accent-cyan)' }} />
          <span style={{ fontSize: '0.8125rem', color: 'var(--text-secondary)', fontWeight: '500' }}>Ollama LLM:</span>
          <select 
            value={selectedModel} 
            onChange={(e) => setSelectedModel(e.target.value)}
            style={{ background: 'transparent', color: 'var(--text-primary)', border: 'none', fontSize: '0.8125rem', fontWeight: '600', outline: 'none', cursor: 'pointer' }}
          >
            {models.map(m => (
              <option key={m.id} value={m.id} style={{ background: '#0f172a', color: '#fff' }}>{m.name}</option>
            ))}
          </select>
        </div>

        {/* Connection Indicator */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: '0.8125rem' }}>
          <Server style={{ width: '16px', height: '16px', color: backendStatus ? 'var(--accent-emerald)' : 'var(--accent-amber)' }} />
          <span className="badge badge-done" style={{ background: backendStatus ? 'rgba(16,185,129,0.15)' : 'rgba(245,158,11,0.15)', color: backendStatus ? '#34d399' : '#fbbf24', border: backendStatus ? '1px solid rgba(16,185,129,0.3)' : '1px solid rgba(245,158,11,0.3)' }}>
            <span style={{ width: '6px', height: '6px', borderRadius: '50%', background: backendStatus ? '#34d399' : '#fbbf24', display: 'inline-block' }}></span>
            {backendStatus ? 'Backend Connected' : 'Connecting API...'}
          </span>
        </div>
        {user && (
          <button className="btn-secondary" onClick={onLogout} title="Đăng xuất" style={{ padding: '0.45rem 0.65rem' }}>
            <LogOut size={14} />
            <span>{user.display_name || user.email}</span>
          </button>
        )}
      </div>
    </header>
  );
}
