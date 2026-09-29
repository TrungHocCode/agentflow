import { LogOut, Sparkles } from 'lucide-react';
import { Button } from './ui/button';

function initials(user) {
  const name = String(user?.display_name || user?.email || 'A').trim();
  return name.slice(0, 1).toUpperCase();
}

export default function Header({ user, onLogout }) {
  return (
    <header className="app-header">
      <div className="brand-lockup" aria-label="AgentFlow">
        <div className="brand-mark" aria-hidden="true">
          <Sparkles />
        </div>
        <div>
          <div className="brand-name">AgentFlow</div>
          <p className="brand-caption">Research, organized</p>
        </div>
      </div>

      {user && (
        <div className="header-account">
          <span className="account-avatar" aria-hidden="true">{initials(user)}</span>
          <span className="account-name" title={user.display_name || user.email}>
            {user.display_name || user.email}
          </span>
          <Button type="button" variant="ghost" size="sm" onClick={onLogout} title="Sign out">
            <LogOut data-icon="inline-start" />
            Sign out
          </Button>
        </div>
      )}
    </header>
  );
}
