import { Badge } from './ui/badge';

const OUTCOME_VARIANTS = {
  changes_detected: 'destructive',
  changed: 'destructive',
  no_change: 'secondary',
  baseline_created: 'default',
  partial: 'outline',
  unavailable: 'outline',
  rebaseline_required: 'outline'
};

const QUALITY_VARIANTS = {
  complete: 'secondary',
  partial: 'outline',
  insufficient: 'destructive'
};

// Lifecycle status is a separate axis from comparison outcome; do not reuse outcome styling.
const STATUS_VARIANTS = {
  active: 'secondary',
  archived: 'outline',
  queued: 'outline',
  pending: 'outline',
  running: 'secondary',
  completed: 'secondary',
  failed: 'destructive',
  cancelled: 'ghost',
  interrupted: 'outline',
  abandoned: 'ghost'
};

export function OutcomeBadge({ outcome }) {
  if (!outcome) return null;
  return <Badge variant={OUTCOME_VARIANTS[outcome] || 'outline'}>{outcome.replaceAll('_', ' ')}</Badge>;
}

export function QualityBadge({ quality }) {
  if (!quality) return null;
  return <Badge variant={QUALITY_VARIANTS[quality] || 'outline'}>{quality}</Badge>;
}

export function VerificationBadge({ verification }) {
  if (!verification) return null;
  return <Badge variant={verification === 'observed' ? 'secondary' : 'destructive'}>{verification}</Badge>;
}

export function StatusBadge({ status }) {
  if (!status) return null;
  return <Badge variant={STATUS_VARIANTS[status] || 'outline'}>{String(status).replaceAll('_', ' ')}</Badge>;
}

export function ApprovalBadge({ status }) {
  if (!status) return null;
  return <Badge variant={status === 'approved' ? 'secondary' : 'outline'}>{String(status).replaceAll('_', ' ')}</Badge>;
}
