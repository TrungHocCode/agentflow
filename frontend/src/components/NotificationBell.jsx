import { useEffect, useState } from 'react';
import { Bell } from 'lucide-react';
import { listNotifications, markNotificationRead } from '../services/ciApi';
import { Button } from './ui/button';
import { Card } from './ui/card';

export default function NotificationBell() {
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState([]);
  const [unread, setUnread] = useState(0);

  async function refresh() {
    try {
      const page = await listNotifications(20, false);
      setItems(page.items || []);
      setUnread(page.items ? page.items.filter((item) => !item.read_at).length : 0);
    } catch {
      // Notifications are best-effort; runs and briefs keep working without them.
    }
  }

  useEffect(() => {
    refresh();
    const timer = window.setInterval(refresh, 30000);
    return () => window.clearInterval(timer);
  }, []);

  async function handleRead(id) {
    try {
      await markNotificationRead(id);
      await refresh();
    } catch {
      // Keep the list as-is on failure.
    }
  }

  return (
    <div className="relative">
      <Button type="button" variant="ghost" size="sm" onClick={() => setOpen((value) => !value)} aria-label="Notifications">
        <Bell data-icon="inline-start" />
        {unread > 0 && <span data-testid="notification-count">{unread}</span>}
      </Button>
      {open && (
        <Card className="absolute right-0 z-50 w-80 p-2" data-testid="notification-list">
          {items.length === 0 ? (
            <p className="p-2 text-sm text-muted-foreground">No notifications yet.</p>
          ) : (
            <ul className="flex max-h-80 flex-col gap-1 overflow-auto">
              {items.map((item) => (
                <li key={item.id}>
                  <button
                    type="button"
                    className="flex w-full flex-col gap-0.5 rounded p-2 text-left text-sm hover:bg-muted"
                    onClick={() => handleRead(item.id)}
                  >
                    <span className="font-medium">{item.read_at ? item.title : `• ${item.title}`}</span>
                    {item.body && <span className="text-muted-foreground">{item.body}</span>}
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
