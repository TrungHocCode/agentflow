import { AlertTriangle, CheckCircle2, FileClock, Hourglass, XCircle } from 'lucide-react';
import { Card } from './ui/card';

const STATES = {
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

export default function EmptyState({ state }) {
  const config = STATES[state] || STATES.unavailable;
  const Icon = config.icon;
  return (
    <Card className="flex flex-col items-center gap-2 p-6 text-center" data-testid={`empty-state-${state}`}>
      <Icon aria-hidden="true" />
      <p className="font-medium">{config.title}</p>
      <p className="text-sm text-muted-foreground">{config.body}</p>
    </Card>
  );
}
