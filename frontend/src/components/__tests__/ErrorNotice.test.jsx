import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import ErrorNotice from '../ErrorNotice';

describe('ErrorNotice', () => {
  it('renders the message and splits an embedded reference into its own line', () => {
    render(<ErrorNotice message="Could not save the watchlist (reference: ERR-123)" />);

    expect(screen.getByText('Could not save the watchlist')).toBeInTheDocument();
    expect(screen.getByTestId('error-reference')).toHaveTextContent('Reference: ERR-123');
  });

  it('renders an explicit reference value', () => {
    render(<ErrorNotice message="Request failed" reference="req-9f2" />);

    expect(screen.getByTestId('error-reference')).toHaveTextContent('Reference: req-9f2');
  });

  it('calls retry and dismiss callbacks', () => {
    const onRetry = vi.fn();
    const onDismiss = vi.fn();
    render(<ErrorNotice message="Request failed" onRetry={onRetry} onDismiss={onDismiss} />);

    fireEvent.click(screen.getByRole('button', { name: /retry/i }));
    fireEvent.click(screen.getByRole('button', { name: /dismiss error/i }));

    expect(onRetry).toHaveBeenCalledTimes(1);
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it('renders nothing without a message', () => {
    const { container } = render(<ErrorNotice message="" onRetry={() => {}} />);
    expect(container).toBeEmptyDOMElement();
  });
});
