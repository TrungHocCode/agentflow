import { describe, expect, it } from 'vitest';
import { dateLabel, revisionLabel, runLabel, sourceLabel } from '../labels';

describe('labels', () => {
  it('formats a run label without leaking a raw UUID', () => {
    const label = runLabel({ run_id: 'abcdef12-3456-7890-abcd-ef1234567890', created_at: '2026-10-15T02:00:00Z' });
    expect(label.startsWith('Run · ')).toBe(true);
    expect(label).toContain('abcdef12');
    expect(label).not.toContain('abcdef12-3456');
  });

  it('falls back to a marker when a run has no id', () => {
    expect(runLabel(null)).toContain('Run ·');
  });

  it('prefers a source name over its identifier', () => {
    expect(sourceLabel({ name: 'Pricing page' })).toBe('Pricing page');
    expect(sourceLabel({ source_id: 'deadbeef-0000' })).toBe('Source deadbeef');
    expect(sourceLabel({})).toBe('Source');
  });

  it('labels revisions', () => {
    expect(revisionLabel({ revision_number: 3 })).toBe('Revision 3');
    expect(revisionLabel(null)).toBe('—');
  });

  it('returns a placeholder for a missing date', () => {
    expect(dateLabel(null)).toBe('—');
  });
});
