import { describe, expect, it } from 'vitest';
import { changeStateFor, worstChangeState } from '../changeState';

describe('changeStateFor', () => {
  it('never claims no change before anything was collected', () => {
    expect(changeStateFor([], []).state).toBe('no_runs');
    expect(changeStateFor([{ run_id: 'a', status: 'running' }], []).state).toBe('running');
  });

  it('reports a failed latest run with its reason', () => {
    const runs = [
      { run_id: 'b', status: 'failed', error_message: 'Out of memory.' },
      { run_id: 'a', status: 'completed' }
    ];
    expect(changeStateFor(runs, [{ run_id: 'a', outcome: 'no_change' }])).toEqual({
      state: 'failed',
      detail: 'Out of memory.'
    });
  });

  it('treats cancelled and interrupted runs as not checked', () => {
    expect(changeStateFor([{ run_id: 'a', status: 'cancelled' }], []).state).toBe('failed');
    expect(changeStateFor([{ run_id: 'a', status: 'interrupted' }], []).state).toBe('failed');
  });

  it('uses the outcome of the latest completed run brief', () => {
    const runs = [{ run_id: 'b', status: 'completed' }];
    expect(changeStateFor(runs, [{ run_id: 'b', outcome: 'baseline_created' }]).state).toBe('baseline_created');
    expect(changeStateFor(runs, [{ run_id: 'b', outcome: 'no_change' }]).state).toBe('no_change');
    expect(changeStateFor(runs, [{ run_id: 'b', outcome: 'partial' }]).state).toBe('partial');
  });

  it('does not claim no change when the completed run has no brief', () => {
    const runs = [{ run_id: 'b', status: 'completed' }];
    expect(changeStateFor(runs, [{ run_id: 'a', outcome: 'no_change' }]).state).toBe('unavailable');
  });
});

describe('worstChangeState', () => {
  it('asks for a watchlist when there are none', () => {
    expect(worstChangeState([]).state).toBe('no_watchlists');
  });

  it('lets one unchecked watchlist outrank a healthy one', () => {
    const failed = { state: 'failed', detail: 'Reason' };
    expect(worstChangeState([{ state: 'no_change' }, failed])).toBe(failed);
    expect(worstChangeState([{ state: 'no_change' }, { state: 'no_runs' }]).state).toBe('no_runs');
  });
});
