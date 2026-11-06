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
    expect(items.length).toBeGreaterThanOrEqual(3);
    expect(items.length).toBeLessThanOrEqual(4);

    const active = within(primaryNav).getByRole('button', { name: /watchlists/i });
    expect(active).toHaveAttribute('aria-current', 'page');
    expect(active).toHaveAttribute('data-active', 'true');

    const inactive = within(primaryNav).getByRole('button', { name: /changes/i });
    expect(inactive).not.toHaveAttribute('aria-current');
  });

  it('shows the chat composer and suggestion cards on the empty state', () => {
    renderChat();

    expect(screen.getByText(/set up competitor tracking/i)).toBeInTheDocument();
    expect(screen.getByLabelText('Research request')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /send request/i })).toBeInTheDocument();

    const suggestions = screen.getByLabelText('Example prompts');
    expect(within(suggestions).getAllByRole('button')).toHaveLength(3);
  });

  it('renders a contextual title in the slim header', () => {
    render(<Header activeTab="studio" />);

    const header = screen.getByTestId('app-header');
    expect(within(header).getByTestId('header-title')).toHaveTextContent('Assistant');
    expect(within(header).getByText('Workspace')).toBeInTheDocument();
  });
});
