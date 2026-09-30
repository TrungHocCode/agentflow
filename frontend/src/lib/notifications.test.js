import { beforeEach, describe, expect, it, vi } from 'vitest';
import { notify, notifyRunFinished } from './notifications';
import { toast } from '../components/ui/toast';

vi.mock('../components/ui/toast', () => ({ toast: { add: vi.fn() } }));
beforeEach(() => vi.clearAllMocks());

describe('notifications', () => {
  it('keeps errors longer and announces them urgently', () => {
    notify({ title: 'Failed', type: 'error' });
    expect(toast.add).toHaveBeenCalledWith(expect.objectContaining({ timeout: 10000, priority: 'high' }));
  });

  it.each(['completed', 'failed', 'interrupted', 'cancelled', 'abandoned'])('notifies terminal status %s with a stable id and action', status => {
    const action = vi.fn();
    notifyRunFinished('run-1', status, action);
    expect(toast.add).toHaveBeenCalledWith(expect.objectContaining({
      id: `run:run-1:${status}`, actionProps: expect.objectContaining({ onClick: action })
    }));
  });

  it('does not notify on every running update', () => {
    notifyRunFinished('run-1', 'running', vi.fn());
    expect(toast.add).not.toHaveBeenCalled();
  });
});
