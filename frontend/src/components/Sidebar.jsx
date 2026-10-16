import { BookOpenText, FileText, GitCompare, MessageSquareText, Radar, Settings } from 'lucide-react';
import { Button } from './ui/button';

const PRIMARY_ITEMS = [
  { id: 'watchlists', label: 'Watchlists', icon: Radar },
  { id: 'changes', label: 'Changes', icon: GitCompare },
  { id: 'briefs', label: 'Briefs & History', icon: FileText },
  { id: 'settings', label: 'Settings', icon: Settings }
];

const SECONDARY_ITEMS = [
  { id: 'studio', label: 'Assistant', icon: MessageSquareText }
];

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

export default function Sidebar({ activeTab, setActiveTab }) {
  return (
    <aside className="workspace-sidebar" aria-label="Primary navigation">
      <div>
        <p className="sidebar-section-label">Workspace</p>
        <nav className="sidebar-nav">
          {PRIMARY_ITEMS.map((item) => (
            <NavButton key={item.id} item={item} activeTab={activeTab} setActiveTab={setActiveTab} />
          ))}
        </nav>
      </div>

      <div>
        <p className="sidebar-section-label">Assistant</p>
        <nav className="sidebar-nav">
          {SECONDARY_ITEMS.map((item) => (
            <NavButton key={item.id} item={item} activeTab={activeTab} setActiveTab={setActiveTab} />
          ))}
        </nav>
      </div>

      <div className="sidebar-note">
        <BookOpenText aria-hidden="true" />
        <span>Track competitor sources, review changes, and read the brief.</span>
      </div>
    </aside>
  );
}
