import React from 'react';
import { Sparkles, LogOut } from 'lucide-react';

export default function Header({ user, onLogout }) {
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
          </div>
          <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>Không gian nghiên cứu thông minh</p>
        </div>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
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
