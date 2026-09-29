import { BookOpenText, Clock3, GitMerge, MessageSquareText, Wrench } from 'lucide-react';
import { Button } from './ui/button';

const NAV_ITEMS = [
  { id: 'studio', label: 'Research', icon: MessageSquareText },
  { id: 'canvas', label: 'Workflow', icon: GitMerge },
  { id: 'catalog', label: 'Catalog', icon: Wrench },
  { id: 'runs', label: 'Run history', icon: Clock3 }
];

export default function Sidebar({ activeTab, setActiveTab }) {
  return (
    <aside className="workspace-sidebar" aria-label="Main navigation">
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
        <span>Ask a question, review the plan, and get a source-backed research report.</span>
      </div>
    </aside>
  );
}
