import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ChatStudio from './ChatStudio';

const scroll = vi.hoisted(() => ({ scrollToBottom: vi.fn(), isAtBottom: true }));
vi.mock('use-stick-to-bottom', () => ({
  useStickToBottom: () => ({ scrollRef: () => {}, contentRef: () => {}, ...scroll })
}));

const props = {
  messages: [{ sender: 'user', text: 'Research models' }], activePlan: [],
  onSendMessage: vi.fn(), onApprovePlan: vi.fn()
};

afterEach(cleanup);
beforeEach(() => { vi.clearAllMocks(); scroll.isAtBottom = true; });

describe('ChatStudio', () => {
  it('Enter sends the request without approving an existing plan', async () => {
    render(<ChatStudio {...props} activePlan={[{ id: 1, description: 'Find official sources', node: 'source_researcher', status: 'pending', dependencies: [] }]} />);
    await userEvent.type(screen.getByRole('textbox', { name: 'Research request' }), 'Compare models{Enter}');
    expect(props.onSendMessage).toHaveBeenCalledWith('Compare models');
    expect(props.onApprovePlan).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Approve and start' })).toBeTruthy();
  });

  it('resumes scrolling for a new user message, not for streaming deltas', () => {
    const view = render(<ChatStudio {...props} />);
    scroll.scrollToBottom.mockClear();
    view.rerender(<ChatStudio {...props} messages={[...props.messages, { sender: 'supervisor', text: 'Streaming', isGenerating: true }]} />);
    expect(scroll.scrollToBottom).not.toHaveBeenCalled();
    view.rerender(<ChatStudio {...props} messages={[...props.messages, { sender: 'supervisor', text: 'Done' }, { sender: 'user', text: 'Next request' }]} />);
    expect(scroll.scrollToBottom).toHaveBeenCalledWith({ animation: 'instant', wait: true });
  });

  it('allows returning to the latest message after scrolling away', async () => {
    scroll.isAtBottom = false;
    render(<ChatStudio {...props} />);
    scroll.scrollToBottom.mockClear();
    await userEvent.click(screen.getByRole('button', { name: 'Jump to latest' }));
    expect(scroll.scrollToBottom).toHaveBeenCalledWith({ animation: 'instant' });
  });

  it('Shift+Enter inserts a newline rather than sending', async () => {
    render(<ChatStudio {...props} />);
    await userEvent.type(screen.getByRole('textbox', { name: 'Research request' }), 'First{Shift>}{Enter}{/Shift}second');
    expect(props.onSendMessage).not.toHaveBeenCalled();
    expect(screen.getByRole('textbox', { name: 'Research request' }).value).toBe('First\nsecond');
  });
});
