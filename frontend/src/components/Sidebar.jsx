import React from 'react';
import { MessageSquare, GitMerge, Wrench, Clock } from 'lucide-react';

export default function Sidebar({ activeTab, setActiveTab }) {
  const navItems = [
    { id: 'studio', label: 'Trò chuyện', icon: MessageSquare },
    { id: 'canvas', label: 'Sơ đồ quy trình', icon: GitMerge },
    { id: 'catalog', label: 'Công cụ & trợ lý', icon: Wrench },
    { id: 'runs', label: 'Lịch sử chạy', icon: Clock }
  ];

  return (
    <aside className="glass-panel" style={{ width: '240px', borderRadius: 0, borderTop: 0, borderBottom: 0, borderLeft: 0, display: 'flex', flexDirection: 'column', padding: '1.25rem 0.75rem' }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.375rem' }}>
        <p style={{ fontSize: '0.6875rem', fontWeight: '700', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.08em', padding: '0 0.75rem 0.5rem 0.75rem' }}>
          Không gian làm việc
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
                padding: '0.75rem 0.875rem',
                borderRadius: 'var(--radius-md)',
                background: isActive ? 'linear-gradient(90deg, rgba(99,102,241,0.2), rgba(6,182,212,0.1))' : 'transparent',
                border: isActive ? '1px solid rgba(99,102,241,0.4)' : '1px solid transparent',
                color: isActive ? '#fff' : 'var(--text-secondary)',
                fontWeight: isActive ? '600' : '500',
                fontSize: '0.875rem',
                cursor: 'pointer',
                transition: 'all 0.2s ease',
                width: '100%',
                textAlign: 'left'
              }}
              aria-current={isActive ? 'page' : undefined}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                <Icon style={{ width: '18px', height: '18px', color: isActive ? 'var(--accent-cyan)' : 'var(--text-muted)' }} />
                <span>{item.label}</span>
              </div>
            </button>
          );
        })}
      </div>
    </aside>
  );
}
