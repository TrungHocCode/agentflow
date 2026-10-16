// Human-readable labels for the primary UI. Keep raw identifiers in secondary/technical views.

export function dateLabel(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
}

function shortId(value) {
  const id = String(value || '');
  return id ? id.slice(0, 8) : '';
}

export function runLabel(run) {
  const when = dateLabel(run?.created_at || run?.updated_at);
  const id = shortId(run?.run_id || run?.id);
  return id ? `Run · ${when} · ${id}` : `Run · ${when}`;
}

export function sourceLabel(source) {
  const name = source?.name || source?.title;
  if (name) return name;
  const id = shortId(source?.source_id || source?.id);
  return id ? `Source ${id}` : 'Source';
}

export function revisionLabel(revision) {
  if (!revision) return '—';
  return `Revision ${revision.revision_number ?? '?'}`;
}
