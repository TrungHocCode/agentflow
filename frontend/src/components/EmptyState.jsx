import { AlertTriangle, CheckCircle2, FileClock, Hourglass, Radar, XCircle } from 'lucide-react';
import { Card } from './ui/card';

const STATES = {
  no_watchlists: {
    icon: Radar,
    title: 'No watchlists yet',
    body: 'Create a watchlist and run it to start detecting changes.'
  },
  no_runs: {
    icon: Radar,
    title: 'Nothing collected yet',
    body: 'Run a watchlist to capture a first baseline. Changes appear from the next run onward.'
  },
  running: {
    icon: Hourglass,
    title: 'Run in progress',
    body: 'Changes appear here when the run finishes.'
  },
  rebaseline_required: {
    icon: AlertTriangle,
    title: 'New baseline required',
    body: 'A source can no longer be compared with its baseline. Treat it as unknown, not unchanged.'
  },
  no_change: {
    icon: CheckCircle2,
    title: 'No change detected',
    body: 'Current snapshots match the pinned baselines for every compared source.'
  },
  baseline_created: {
    icon: FileClock,
    title: 'Baseline created',
    body: 'First eligible captures were recorded as baselines. Future runs compare against them.'
  },
  partial: {
    icon: Hourglass,
    title: 'Partial coverage',
    body: 'Some sources could not be compared. Treat uncovered sources as unknown, not unchanged.'
  },
  unavailable: {
    icon: XCircle,
    title: 'Comparison unavailable',
    body: 'No eligible snapshot exists yet. Inspect fetch outcomes before rerunning.'
  },
  failed: {
    icon: AlertTriangle,
    title: 'Run failed',
    body: 'The run did not complete. Its scope and baselines are unchanged; retry explicitly.'
  }
};

export default function EmptyState({ state, detail }) {
  const config = STATES[state] || STATES.unavailable;
  const Icon = config.icon;
  return (
    <Card className="empty-state-fade empty-state" data-testid={`empty-state-${state}`}>
      <span className="empty-state__icon">
        <Icon aria-hidden="true" />
      </span>
      <p className="empty-state__title">{config.title}</p>
      <p className="empty-state__description">{config.body}</p>
      {detail && <p className="empty-state__description">{detail}</p>}
    </Card>
  );
}
