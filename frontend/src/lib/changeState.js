const ACTIVE_RUN_STATUSES = new Set(['queued', 'running', 'pending']);
const EMPTY_FEED_OUTCOMES = new Set(['baseline_created', 'no_change', 'partial', 'rebaseline_required', 'unavailable']);

// Most severe first: one healthy watchlist must not hide another that could not be checked.
const SEVERITY = ['failed', 'partial', 'unavailable', 'rebaseline_required', 'running', 'no_runs', 'baseline_created', 'no_change'];

/**
 * Explain an empty change feed from what actually happened. An empty feed only means
 * "no change" when the latest run finished and its brief compared the sources.
 */
export function changeStateFor(runs = [], briefs = []) {
  const latest = runs[0];
  if (!latest) return { state: 'no_runs' };
  if (ACTIVE_RUN_STATUSES.has(latest.status)) return { state: 'running' };
  if (latest.status !== 'completed') return { state: 'failed', detail: latest.error_message || '' };
  const outcome = briefs.find((brief) => brief.run_id === latest.run_id)?.outcome;
  return { state: EMPTY_FEED_OUTCOMES.has(outcome) ? outcome : 'unavailable' };
}

export function worstChangeState(entries) {
  if (entries.length === 0) return { state: 'no_watchlists' };
  const rank = (entry) => SEVERITY.indexOf(entry.state);
  return entries.reduce((worst, entry) => (rank(entry) < rank(worst) ? entry : worst));
}
