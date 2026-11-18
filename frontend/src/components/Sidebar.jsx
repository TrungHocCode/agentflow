import {
  BookOpenText,
  FileText,
  GitCompare,
  LogOut,
  MessageSquareText,
  Radar,
  Settings,
  Sparkles
} from 'lucide-react';
import { Button } from './ui/button';

// The assistant is the main way in; the pages below are for following what it set up.
const PRIMARY_ITEMS = [
  { id: 'studio', label: 'Assistant', icon: MessageSquareText },
  { id: 'changes', label: 'Changes', icon: GitCompare },
  { id: 'watchlists', label: 'Watchlists', icon: Radar },
  { id: 'briefs', label: 'Briefs & History', icon: FileText },
  { id: 'settings', label: 'Settings', icon: Settings }
];

function initials(user) {
  const name = String(user?.display_name || user?.email || 'A').trim();
  return name.slice(0, 1).toUpperCase();
}

function NavButton({ item, activeTab, setActiveTab }) {
  const isActive = activeTab === item.id;
  const Icon = item.icon;
  return (
    <Button
      type="button"
      variant="ghost"
      className="sidebar-link"
      data-active={isActive}
      aria-current={isActive ? 'page' : undefined}
      onClick={() => setActiveTab(item.id)}
    >
      <Icon data-icon="inline-start" />
      {item.label}
    </Button>
  );
}

export default function Sidebar({ activeTab, setActiveTab, user, onLogout }) {
  const displayName = user?.display_name || user?.email;

  return (
    <aside className="workspace-sidebar" aria-label="Primary navigation">
      <div className="sidebar-main">
        <div className="sidebar-brand" aria-label="AgentFlow">
          <div className="sidebar-brand__mark" aria-hidden="true">
            <Sparkles />
          </div>
          <div>
            <div className="sidebar-brand__name">AgentFlow</div>
            <p className="sidebar-brand__caption">Competitive intelligence</p>
          </div>
        </div>

        <div>
          <p className="sidebar-section-label">Workspace</p>
          <nav className="sidebar-nav" data-testid="sidebar-primary-nav">
            {PRIMARY_ITEMS.map((item) => (
              <NavButton key={item.id} item={item} activeTab={activeTab} setActiveTab={setActiveTab} />
            ))}
          </nav>
        </div>

        <div className="sidebar-note">
          <BookOpenText aria-hidden="true" />
          <span>Tell the assistant what to track, then follow changes and briefs here.</span>
        </div>
      </div>

      {user && (
        <div className="sidebar-footer">
          <div className="sidebar-user">
            <span className="sidebar-user__avatar" aria-hidden="true">{initials(user)}</span>
            <span className="sidebar-user__identity">
              <span className="sidebar-user__name" title={displayName}>{displayName}</span>
              <span className="sidebar-user__meta">Signed in</span>
            </span>
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              className="sidebar-signout"
              onClick={onLogout}
              title="Sign out"
              aria-label="Sign out"
            >
              <LogOut />
            </Button>
          </div>
        </div>
      )}
    </aside>
  );
}
