import React from 'react';
import { MessageSquare, GitMerge, Layers, PlayCircle, HelpCircle } from 'lucide-react';

export default function Sidebar({ activeTab, setActiveTab }) {
  const navItems = [
    { id: 'studio', label: 'Conversational Studio', icon: MessageSquare, badge: 'AI Chat' },
    { id: 'canvas', label: 'Flow Canvas (DAG)', icon: GitMerge, badge: 'Visual' },
    { id: 'catalog', label: 'Agents & Tools Catalog', icon: Layers, badge: '8 Tools' },
    { id: 'runs', label: 'Execution Runs (SSE)', icon: PlayCircle, badge: 'Realtime' }
  ];

  return (
    <aside className="glass-panel" style={{ width: '260px', borderRadius: 0, borderTop: 0, borderBottom: 0, borderLeft: 0, display: 'flex', flexDirection: 'column', justifyContent: 'space-between', padding: '1.25rem 0.75rem' }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.375rem' }}>
        <p style={{ fontSize: '0.6875rem', fontWeight: '700', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.08em', padding: '0 0.75rem 0.5rem 0.75rem' }}>
          Platform Navigation
        </p>

        {navItems.map((item) => {
          const Icon = item.icon;
          const isActive = activeTab === item.id;
          return (
            <button
              key={item.id}
              onClick={() => setActiveTab(item.id)}
              style={{
                display: 'flex',
                alignItems: 'center',
                justify: 'space-between',
                padding: '0.75rem 0.875rem',
                borderRadius: 'var(--radius-md)',
                background: isActive ? 'linear-gradient(90deg, rgba(99,102,241,0.2), rgba(6,182,212,0.1))' : 'transparent',
                border: isActive ? '1px solid rgba(99,102,241,0.4)' : '1px solid transparent',
                color: isActive ? '#fff' : 'var(--text-secondary)',
                fontWeight: isActive ? '600' : '500',
                fontSize: '0.875rem',
                cursor: 'pointer',
                transition: 'all 0.2s ease'
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                <Icon style={{ width: '18px', height: '18px', color: isActive ? 'var(--accent-cyan)' : 'var(--text-muted)' }} />
                <span>{item.label}</span>
              </div>
              <span 
                style={{ 
                  fontSize: '0.6875rem', 
                  padding: '0.125rem 0.5rem', 
                  borderRadius: '9999px',
                  background: isActive ? 'rgba(99,102,241,0.3)' : 'rgba(255,255,255,0.05)',
                  color: isActive ? '#c7d2fe' : 'var(--text-muted)' 
                }}
              >
                {item.badge}
              </span>
            </button>
          );
        })}
      </div>

      {/* Footer Info */}
      <div className="glass-card" style={{ padding: '0.875rem', margin: '0 0.25rem' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.375rem' }}>
          <HelpCircle style={{ width: '16px', height: '16px', color: 'var(--accent-indigo)' }} />
          <span style={{ fontSize: '0.8125rem', fontWeight: '600', color: 'var(--text-primary)' }}>Architecture</span>
        </div>
        <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)', lineHeight: 1.4 }}>
          Two-Phase Supervisor-Worker model with deterministic TaskDispatcher.
        </p>
      </div>
    </aside>
  );
}
