import { BookOpenText, Clock3, GitMerge, MessageSquareText, Radar, Wrench } from 'lucide-react';
import { Button } from './ui/button';

const NAV_ITEMS = [
  { id: 'studio', label: 'Research', icon: MessageSquareText },
  { id: 'intelligence', label: 'Intelligence', icon: Radar },
  { id: 'canvas', label: 'Workflow', icon: GitMerge },
  { id: 'catalog', label: 'Tools', icon: Wrench },
  { id: 'runs', label: 'History', icon: Clock3 }
];

export default function Sidebar({ activeTab, setActiveTab }) {
  return (
    <aside className="workspace-sidebar" aria-label="Primary navigation">
      <div>
        <p className="sidebar-section-label">Workspace</p>
        <nav className="sidebar-nav">
          {NAV_ITEMS.map(({ id, label, icon: Icon }) => {
            const isActive = activeTab === id;
            return (
              <Button
                key={id}
                type="button"
                variant="ghost"
                className="sidebar-link"
                data-active={isActive}
                aria-current={isActive ? 'page' : undefined}
                onClick={() => setActiveTab(id)}
              >
                <Icon data-icon="inline-start" />
                {label}
              </Button>
            );
          })}
        </nav>
      </div>

      <div className="sidebar-note">
        <BookOpenText aria-hidden="true" />
        <span>Ask a question, review the plan, then receive a sourced research report.</span>
      </div>
    </aside>
  );
}
