import { BookOpenText, Clock3, GitMerge, MessageSquareText, Wrench } from 'lucide-react';
import { Button } from './ui/button';

const NAV_ITEMS = [
  { id: 'studio', label: 'Nghiên cứu', icon: MessageSquareText },
  { id: 'canvas', label: 'Quy trình', icon: GitMerge },
  { id: 'catalog', label: 'Công cụ', icon: Wrench },
  { id: 'runs', label: 'Lịch sử', icon: Clock3 }
];

export default function Sidebar({ activeTab, setActiveTab }) {
  return (
    <aside className="workspace-sidebar" aria-label="Điều hướng chính">
      <div>
        <p className="sidebar-section-label">Không gian làm việc</p>
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
        <span>Đặt câu hỏi, xem lại kế hoạch rồi nhận báo cáo nghiên cứu có nguồn.</span>
      </div>
    </aside>
  );
}
