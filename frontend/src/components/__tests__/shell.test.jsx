import { render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import Sidebar from '../Sidebar';
import Header from '../Header';
import ChatStudio from '../ChatStudio';

// The bell polls notifications on mount; keep the shell test focused on layout.
vi.mock('../NotificationBell', () => ({
  default: () => <span data-testid="notification-bell" />
}));

const noop = () => {};

function renderChat(overrides = {}) {
  return render(
    <ChatStudio
      messages={[]}
      onSendMessage={noop}
      onApprovePlan={noop}
      onOpenResults={noop}
      onViewRunDetails={noop}
      onCancelRun={noop}
      onCancelTurn={noop}
      onDeleteConversation={noop}
      conversationId={null}
      isCancelling={false}
      isDeletingConversation={false}
      isRestoring={false}
      isProcessing={false}
      isStreaming={false}
      activeTurnId={null}
      activePlan={[]}
      runStatus={null}
      runId={null}
      onWatchlistCreated={noop}
      {...overrides}
    />
  );
}

describe('application shell', () => {
  it('renders the primary navigation with an active pill', () => {
    render(<Sidebar activeTab="watchlists" setActiveTab={noop} />);

    const primaryNav = screen.getByTestId('sidebar-primary-nav');
    const items = within(primaryNav).getAllByRole('button');
    expect(items.map((item) => item.textContent)).toEqual(
      ['Assistant', 'Changes', 'Watchlists', 'Briefs & History', 'Settings']
    );

    const active = within(primaryNav).getByRole('button', { name: /watchlists/i });
    expect(active).toHaveAttribute('aria-current', 'page');
    expect(active).toHaveAttribute('data-active', 'true');

    const inactive = within(primaryNav).getByRole('button', { name: /changes/i });
    expect(inactive).not.toHaveAttribute('aria-current');
  });

  it('shows the chat composer and suggestion cards on the empty state', () => {
    renderChat();

    expect(screen.getByText(/set up competitor tracking/i)).toBeInTheDocument();
    expect(screen.getByLabelText('Competitor intelligence request')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /send request/i })).toBeInTheDocument();

    const suggestions = screen.getByLabelText('Example prompts');
    expect(within(suggestions).getAllByRole('button')).toHaveLength(3);
  });

  it('labels the assistant for competitive intelligence, not generic research', () => {
    renderChat();

    expect(screen.getByText('CI assistant')).toBeInTheDocument();
    expect(screen.getByText(/ask about your competitors/i)).toBeInTheDocument();
    expect(screen.queryByText('Research assistant')).not.toBeInTheDocument();
  });

  it('offers a new chat and lists recent conversations with delete actions', () => {    const onNewChat = vi.fn();
    const onSelectConversation = vi.fn();
    const onDeleteListItem = vi.fn();
    renderChat({
      conversations: [
        { id: 'conv-old', title: 'Old pricing chat', updated_at: '2026-01-01T00:00:00Z' },
        { id: 'conv-new', title: 'ClickUp watch', updated_at: '2026-02-01T00:00:00Z' }
      ],
      conversationId: 'conv-new',
      onNewChat,
      onSelectConversation,
      onDeleteListItem
    });

    screen.getByRole('button', { name: /new chat/i }).click();
    expect(onNewChat).toHaveBeenCalledTimes(1);

    const list = screen.getByText('Old pricing chat').closest('ul');
    expect(list).toBeInTheDocument();
    screen.getByRole('button', { name: /delete chat old pricing chat/i }).click();
    expect(onDeleteListItem).toHaveBeenCalledWith('conv-old');
  });

  it('renders a contextual title in the slim header', () => {
    render(<Header activeTab="studio" />);

    const header = screen.getByTestId('app-header');
    expect(within(header).getByTestId('header-title')).toHaveTextContent('Assistant');
    expect(within(header).getByText('Workspace')).toBeInTheDocument();
  });

  it('hides the page description once the thread is active', () => {
    renderChat({ messages: [{ sender: 'user', text: 'Hi', renderKey: 'm-0' }] });

    expect(screen.queryByText(/answers from approved sources/i)).not.toBeInTheDocument();
  });

  it('shows the setup card only inside the assistant answer that carries a setup result', () => {
    const user = { sender: 'user', text: 'Track my competitors', renderKey: 'm-0' };
    const { rerender } = renderChat({ messages: [user] });
    expect(screen.queryByTestId('chat-proposal')).not.toBeInTheDocument();

    const setup = { product: { name: 'TaskFlow', source: 'default', my_product_id: 'p1' },
      recommendation: { products: [], warnings: [], follow_up_question: 'What does TaskFlow do?' } };
    rerender(
      <ChatStudio
        messages={[user, { sender: 'supervisor', text: 'Using: TaskFlow (default)', ciSetup: setup }]}
        onSendMessage={noop}
        onApprovePlan={noop}
        onOpenResults={noop}
        onViewRunDetails={noop}
        onCancelRun={noop}
        onCancelTurn={noop}
        onDeleteConversation={noop}
        conversationId="conv-1"
        isCancelling={false}
        isDeletingConversation={false}
        isRestoring={false}
        isProcessing={false}
        isStreaming={false}
        activeTurnId={null}
        activePlan={[]}
        runStatus={null}
        runId={null}
        onWatchlistCreated={noop}
      />
    );
    expect(screen.getByTestId('chat-proposal')).toBeInTheDocument();
    expect(screen.getByTestId('chat-proposal-using')).toHaveTextContent('Using: TaskFlow (default)');
  });
});
