import { renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { getCatalogAgents, getCatalogTools } from '../../services/api';
import { useCatalogs } from '../useCatalogs';

vi.mock('../../services/api', () => ({
  getCatalogTools: vi.fn(),
  getCatalogAgents: vi.fn()
}));

describe('useCatalogs', () => {
  afterEach(() => vi.clearAllMocks());

  it('loads catalog data and marks the backend reachable', async () => {
    getCatalogTools.mockResolvedValue([{ name: 'web_search' }]);
    getCatalogAgents.mockResolvedValue([{ name: 'source_researcher' }]);

    const { result } = renderHook(() => useCatalogs());

    await waitFor(() => expect(result.current.backendStatus).toBe(true));
    expect(result.current.tools).toHaveLength(1);
    expect(result.current.agents).toHaveLength(1);
  });

  it('falls back to bundled catalogs when the backend is unreachable', async () => {
    getCatalogTools.mockRejectedValue(new Error('offline'));
    getCatalogAgents.mockRejectedValue(new Error('offline'));

    const { result } = renderHook(() => useCatalogs());

    await waitFor(() => expect(result.current.tools.length).toBeGreaterThan(0));
    expect(result.current.backendStatus).toBe(false);
    expect(result.current.agents.length).toBeGreaterThan(0);
  });
});
