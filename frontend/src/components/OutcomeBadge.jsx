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

const OUTCOME_TONES = {
  changes_detected: 'alert',
  changed: 'alert',
  no_change: 'calm',
  baseline_created: 'info',
  partial: 'warn',
  unavailable: 'quiet',
  rebaseline_required: 'warn'
};

const QUALITY_TONES = {
  complete: 'calm',
  partial: 'warn',
  insufficient: 'alert'
};

const STATUS_TONES = {
  active: 'calm',
  archived: 'quiet',
  queued: 'warn',
  pending: 'warn',
  running: 'info',
  completed: 'calm',
  failed: 'alert',
  cancelled: 'quiet',
  interrupted: 'warn',
  abandoned: 'quiet'
};

function pill(tone) {
  return tone ? `ci-pill ci-pill--${tone}` : 'ci-pill';
}

export function OutcomeBadge({ outcome }) {
  if (!outcome) return null;
  return (
    <Badge variant={OUTCOME_VARIANTS[outcome] || 'outline'} className={pill(OUTCOME_TONES[outcome])}>
      {outcome.replaceAll('_', ' ')}
    </Badge>
  );
}

export function QualityBadge({ quality }) {
  if (!quality) return null;
  return (
    <Badge variant={QUALITY_VARIANTS[quality] || 'outline'} className={pill(QUALITY_TONES[quality])}>
      {quality}
    </Badge>
  );
}

export function VerificationBadge({ verification }) {
  if (!verification) return null;
  return (
    <Badge
      variant={verification === 'observed' ? 'secondary' : 'destructive'}
      className={pill(verification === 'observed' ? 'calm' : 'warn')}
    >
      {verification}
    </Badge>
  );
}

export function StatusBadge({ status }) {
  if (!status) return null;
  return (
    <Badge variant={STATUS_VARIANTS[status] || 'outline'} className={pill(STATUS_TONES[status])}>
      {String(status).replaceAll('_', ' ')}
    </Badge>
  );
}

export function ApprovalBadge({ status }) {
  if (!status) return null;
  return (
    <Badge
      variant={status === 'approved' ? 'secondary' : 'outline'}
      className={pill(status === 'approved' ? 'calm' : 'warn')}
    >
      {String(status).replaceAll('_', ' ')}
    </Badge>
  );
}
