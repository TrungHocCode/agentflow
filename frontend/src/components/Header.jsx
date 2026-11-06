import { ChevronRight } from 'lucide-react';
import NotificationBell from './NotificationBell';

const SECTIONS = {
  watchlists: { crumb: 'Intelligence', title: 'Watchlists' },
  changes: { crumb: 'Intelligence', title: 'Changes' },
  briefs: { crumb: 'Intelligence', title: 'Briefs & History' },
  studio: { crumb: 'Workspace', title: 'Assistant' },
  runs: { crumb: 'Intelligence', title: 'Run details' },
  settings: { crumb: 'Workspace', title: 'Settings' }
};

// Slim contextual bar: the product mark and account live in the sidebar, so the
// header only carries orientation and global actions.
export default function Header({ activeTab = 'watchlists' }) {
  const section = SECTIONS[activeTab] || SECTIONS.watchlists;

  return (
    <header className="app-header" data-testid="app-header">
      <div className="header-context">
        <p className="header-breadcrumb">
          <span>{section.crumb}</span>
          <ChevronRight aria-hidden="true" />
          <span>{section.title}</span>
        </p>
        <h1 className="header-title" data-testid="header-title">{section.title}</h1>
      </div>

      <div className="header-actions">
        <NotificationBell />
      </div>
    </header>
  );
}
