import { useEffect, useRef, useState } from 'react';
import { Bell } from 'lucide-react';
import { listNotifications, markNotificationRead } from '../services/ciApi';
import { Button } from './ui/button';
import { Card } from './ui/card';
import ErrorNotice from './ErrorNotice';
import ListSkeleton from './ListSkeleton';

export default function NotificationBell() {
  const containerRef = useRef(null);
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState([]);
  const [unread, setUnread] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  async function refresh() {
    try {
      const page = await listNotifications(20, false);
      setItems(page.items || []);
      setUnread(page.items ? page.items.filter((item) => !item.read_at).length : 0);
      setError('');
    } catch (err) {
      // Notifications are best-effort; runs and briefs keep working without them.
      setError(err.message || 'Notifications are unavailable right now.');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
    const timer = window.setInterval(refresh, 30000);
    return () => window.clearInterval(timer);
  }, []);

  // Dismiss like any popover, so it cannot linger over the next page.
  useEffect(() => {
    if (!open) return undefined;
    function closeOnOutsidePress(event) {
      if (!containerRef.current?.contains(event.target)) setOpen(false);
    }
    function closeOnEscape(event) {
      if (event.key === 'Escape') setOpen(false);
    }
    document.addEventListener('pointerdown', closeOnOutsidePress);
    document.addEventListener('keydown', closeOnEscape);
    return () => {
      document.removeEventListener('pointerdown', closeOnOutsidePress);
      document.removeEventListener('keydown', closeOnEscape);
    };
  }, [open]);

  async function handleRead(id) {
    try {
      await markNotificationRead(id);
      await refresh();
    } catch (err) {
      // Keep the list as-is on failure but surface the reference for support.
      setError(err.message || 'Could not mark the notification as read.');
    }
  }

  return (
    <div className="relative" ref={containerRef}>
      <Button type="button" variant="ghost" size="sm" onClick={() => setOpen((value) => !value)} aria-label="Notifications">
        <Bell data-icon="inline-start" />
        {unread > 0 && <span className="notification-count" data-testid="notification-count">{unread}</span>}
      </Button>
      {open && (
        <Card className="notification-popover absolute right-0 z-50 w-80 p-2" data-testid="notification-list">
          <div className="ci-section-head px-2 pt-1">
            <p className="ci-section-title">Notifications</p>
            {unread > 0 && <span className="ci-chip ci-chip--quiet">{unread} unread</span>}
          </div>
          {loading ? (
            <ListSkeleton label="Loading notifications" rows={3} compact />
          ) : error ? (
            <ErrorNotice
              message={error}
              onRetry={refresh}
              onDismiss={() => setError('')}
            />
          ) : items.length === 0 ? (
            <p className="ci-empty empty-state-fade">No notifications yet.</p>
          ) : (
            <ul className="notification-list">
              {items.map((item) => (
                <li key={item.id}>
                  <button
                    type="button"
                    className="notification-item"
                    onClick={() => handleRead(item.id)}
                  >
                    <span className="notification-item__title" data-unread={!item.read_at}>{item.title}</span>
                    {item.body && <span className="notification-item__body">{item.body}</span>}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>
      )}
    </div>
  );
}
