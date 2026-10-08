import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import EmptyState from '../EmptyState';
import { OutcomeBadge, QualityBadge, VerificationBadge } from '../OutcomeBadge';

describe('OutcomeBadge', () => {
  it.each([
    ['changes_detected'],
    ['changed'],
    ['no_change'],
    ['baseline_created'],
    ['partial'],
    ['unavailable'],
    ['rebaseline_required']
  ])('renders outcome %s without claiming verification', (outcome) => {
    render(<OutcomeBadge outcome={outcome} />);
    expect(screen.getByText(outcome.replaceAll('_', ' '))).toBeInTheDocument();
  });

  it('renders nothing for missing outcome', () => {
    const { container } = render(<OutcomeBadge outcome={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('renders quality and verification labels', () => {
    render(<QualityBadge quality="complete" />);
    render(<VerificationBadge verification="observed" />);
    expect(screen.getByText('complete')).toBeInTheDocument();
    expect(screen.getByText('observed')).toBeInTheDocument();
  });
});

describe('EmptyState', () => {
  it.each([
    ['no_change', 'No change detected'],
    ['baseline_created', 'Baseline created'],
    ['partial', 'Partial coverage'],
    ['unavailable', 'Comparison unavailable'],
    ['failed', 'Run failed']
  ])('explains %s independently of run lifecycle', (state, title) => {
    render(<EmptyState state={state} />);
    expect(screen.getByTestId(`empty-state-${state}`)).toBeInTheDocument();
    expect(screen.getByText(title)).toBeInTheDocument();
  });
});
