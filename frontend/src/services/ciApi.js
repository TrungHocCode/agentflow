/**
 * Competitive Intelligence API client. Mirrors services/api.js transport behavior
 * (bearer auth, 401 expiry broadcast, {error} payload formatting).
 */

const API_BASE = '/api/v1';
const AUTH_EXPIRED_EVENT = 'agentflow:auth-expired';

function broadcastAuthExpired() {
  localStorage.removeItem('agentflow_access_token');
  localStorage.removeItem('agentflow_user');
  window.dispatchEvent(new window.Event(AUTH_EXPIRED_EVENT));
}

function formatCiError(statusCode, body, fallback) {
  let message = body || fallback;
  try {
    const payload = JSON.parse(body);
    const error = payload?.error;
    if (error && typeof error === 'object') {
      message = error.message || error.detail || message;
      if (error.error_id) message = `${message} (reference: ${error.error_id})`;
    } else if (typeof payload?.detail === 'string') {
      message = payload.detail;
    }
  } catch {
    // Preserve non-JSON error bodies.
  }
  return new Error(`${statusCode}: ${message || fallback}`);
}

async function ciRequest(path, options = {}) {
  const token = localStorage.getItem('agentflow_access_token');
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: { ...(options.headers || {}), ...(token ? { Authorization: `Bearer ${token}` } : {}) }
  });
  if (res.status === 401) broadcastAuthExpired();
  if (!res.ok) {
    const detail = await res.text();
    throw formatCiError(res.status, detail, res.statusText);
  }
  if (res.status === 204) return null;
  return res.json();
}

function json(method, body) {
  return { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
}

const enc = encodeURIComponent;

export async function listWatchlists(limit = 50, offset = 0) {
  return ciRequest(`/watchlists?limit=${limit}&offset=${offset}`);
}

export async function getWatchlist(watchlistId) {
  return ciRequest(`/watchlists/${enc(watchlistId)}`);
}

export async function createWatchlist(payload) {
  return ciRequest('/watchlists', json('POST', payload));
}

export async function updateWatchlist(watchlistId, payload) {
  return ciRequest(`/watchlists/${enc(watchlistId)}`, json('PATCH', payload));
}

export async function archiveWatchlist(watchlistId) {
  return ciRequest(`/watchlists/${enc(watchlistId)}`, { method: 'DELETE' });
}

export async function listRevisions(watchlistId) {
  return ciRequest(`/watchlists/${enc(watchlistId)}/revisions`);
}

export async function approveRevision(watchlistId, revisionId, workflowVersionId) {
  return ciRequest(
    `/watchlists/${enc(watchlistId)}/revisions/${enc(revisionId)}/approval`,
    json('POST', { workflow_version_id: workflowVersionId })
  );
}

export async function listWatchlistRuns(watchlistId, limit = 20, offset = 0) {
  return ciRequest(`/watchlists/${enc(watchlistId)}/runs?limit=${limit}&offset=${offset}`);
}

export async function startWatchlistRun(watchlistId, revisionId, workflowVersionId, idempotencyKey = null) {
  const headers = idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : {};
  return ciRequest(`/watchlists/${enc(watchlistId)}/runs`, {
    ...json('POST', { revision_id: revisionId, workflow_version_id: workflowVersionId }),
    headers: { 'Content-Type': 'application/json', ...headers }
  });
}

export async function listChanges(watchlistId, limit = 50, offset = 0) {
  return ciRequest(`/watchlists/${enc(watchlistId)}/changes?limit=${limit}&offset=${offset}`);
}

export async function getChange(changeId) {
  return ciRequest(`/changes/${enc(changeId)}`);
}

export async function listBriefs(watchlistId, limit = 50, offset = 0) {
  return ciRequest(`/watchlists/${enc(watchlistId)}/briefs?limit=${limit}&offset=${offset}`);
}

export async function buildBrief(watchlistId, runId, revisionId) {
  return ciRequest(`/watchlists/${enc(watchlistId)}/briefs`, json('POST', { run_id: runId, revision_id: revisionId }));
}

export async function getBrief(briefId) {
  return ciRequest(`/briefs/${enc(briefId)}`);
}

export async function downloadBrief(briefId) {
  const token = localStorage.getItem('agentflow_access_token');
  const response = await fetch(`${API_BASE}/briefs/${enc(briefId)}/download`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {}
  });
  if (response.status === 401) broadcastAuthExpired();
  if (!response.ok) {
    const detail = await response.text();
    throw formatCiError(response.status, detail, response.statusText);
  }
  return response.blob();
}

export async function listSnapshots(sourceId, limit = 20) {
  return ciRequest(`/sources/${enc(sourceId)}/snapshots?limit=${limit}`);
}

export async function getSnapshot(snapshotId) {
  return ciRequest(`/snapshots/${enc(snapshotId)}`);
}

export async function getSnapshotContent(snapshotId, representation = 'normalized', start = 0, limit = 4000) {
  return ciRequest(
    `/snapshots/${enc(snapshotId)}/content?representation=${representation}&start=${start}&limit=${limit}`
  );
}

export async function listInvestigationRounds(runId) {
  return ciRequest(`/runs/${enc(runId)}/investigation-rounds`);
}

export function saveBlob(blob, filename) {
  const url = window.URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => window.URL.revokeObjectURL(url), 1000);
}
