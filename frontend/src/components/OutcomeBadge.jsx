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
