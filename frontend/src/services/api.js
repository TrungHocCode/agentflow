/**
 * AgentFlow API Client Service
 * Connects Frontend UI to FastAPI Backend REST API & Realtime SSE Stream.
 */

const API_BASE = '/api/v1';
const AUTH_EXPIRED_EVENT = 'agentflow:auth-expired';
let authSessionGeneration = 0;

function clearStoredSession() {
  authSessionGeneration += 1;
  localStorage.removeItem('agentflow_access_token');
  localStorage.removeItem('agentflow_user');
  window.dispatchEvent(new window.Event(AUTH_EXPIRED_EVENT));
}

function expireSessionIfCurrent(token, requestGeneration) {
  if (
    requestGeneration === authSessionGeneration
    && localStorage.getItem('agentflow_access_token') === (token || null)
  ) {
    clearStoredSession();
  }
}

function storeAccessToken(token) {
  authSessionGeneration += 1;
  localStorage.setItem('agentflow_access_token', token);
}

function withAuthHeaders(headers = {}, token = localStorage.getItem('agentflow_access_token')) {
  return {
    ...headers,
    ...(token ? { Authorization: `Bearer ${token}` } : {})
  };
}

async function requestJson(
  url,
  options = {},
  { authenticated = true, notifyOnUnauthorized = true } = {}
) {
  const requestGeneration = authSessionGeneration;
  const token = authenticated ? localStorage.getItem('agentflow_access_token') : null;
  const headers = authenticated
    ? withAuthHeaders(options.headers, token)
    : { ...(options.headers || {}) };
  const res = await fetch(url, { ...options, headers });
  if (res.status === 401 && notifyOnUnauthorized) {
    expireSessionIfCurrent(token, requestGeneration);
  }
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`${res.status}: ${detail || res.statusText}`);
  }
  if (res.status === 204) return null;
  return res.json();
}

export async function login(email, password) {
  authSessionGeneration += 1;
  const response = await requestJson(`${API_BASE}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password })
  }, { authenticated: false, notifyOnUnauthorized: false });
  storeAccessToken(response.access_token);
  return response.user;
}

export async function register(email, password, displayName) {
  authSessionGeneration += 1;
  const response = await requestJson(`${API_BASE}/auth/register`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password, display_name: displayName })
  }, { authenticated: false, notifyOnUnauthorized: false });
  storeAccessToken(response.access_token);
  return response.user;
}

export async function logout() {
  authSessionGeneration += 1;
  try {
    await requestJson(
      `${API_BASE}/auth/logout`,
      { method: 'POST' },
      { notifyOnUnauthorized: false }
    );
  } finally {
    localStorage.removeItem('agentflow_access_token');
  }
}

export async function getCatalogTools() {
  const res = await fetch(`${API_BASE}/catalog/tools`);
  if (!res.ok) throw new Error('Failed to fetch tools catalog');
  return res.json();
}

export async function getCatalogAgents() {
  const res = await fetch(`${API_BASE}/catalog/agents`);
  if (!res.ok) throw new Error('Failed to fetch agents catalog');
  return res.json();
}

export async function getFlows() {
  return requestJson(`${API_BASE}/flows`);
}

export async function getRuns() {
  return requestJson(`${API_BASE}/runs`);
}

export async function cancelRun(runId) {
  return requestJson(`${API_BASE}/runs/${encodeURIComponent(runId)}/cancel`, {
    method: 'POST'
  });
}

export async function getRunDetails(runId) {
  return requestJson(`${API_BASE}/runs/${runId}`);
}

export async function getRunResults(runId) {
  return requestJson(`${API_BASE}/runs/${runId}/results`);
}

export async function getRunEvidence(runId) {
  return requestJson(`${API_BASE}/runs/${runId}/evidence`);
}

export async function getRunArtifacts(runId) {
  return requestJson(`${API_BASE}/runs/${runId}/artifacts`);
}

export async function downloadRunArtifact(runId, artifactId) {
  const requestGeneration = authSessionGeneration;
  const token = localStorage.getItem('agentflow_access_token');
  const response = await fetch(
    `${API_BASE}/runs/${encodeURIComponent(runId)}/artifacts/${encodeURIComponent(artifactId)}/download`,
    { headers: withAuthHeaders({}, token) }
  );
  if (response.status === 401) expireSessionIfCurrent(token, requestGeneration);
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`${response.status}: ${detail || response.statusText}`);
  }
  return response.blob();
}

export async function retryRun(runId) {
  return requestJson(`${API_BASE}/runs/${runId}/retry`, { method: 'POST' });
}

export async function createWorkflowRun(workflowId, inputData = {}, metadata = {}, conversationId = null) {
  return requestJson(`${API_BASE}/workflows/${workflowId}/runs`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Idempotency-Key': crypto.randomUUID()
    },
    body: JSON.stringify({ input_data: inputData, metadata, conversation_id: conversationId })
  });
}

export async function createWorkflow(name, steps, description = '') {
  return requestJson(`${API_BASE}/workflows`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      name,
      description,
      definition: { steps }
    })
  });
}

export async function createConversation(title = '', workflowId = null) {
  return requestJson(`${API_BASE}/conversations`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title, workflow_id: workflowId })
  });
}

export async function getConversations(limit = 100) {
  return requestJson(`${API_BASE}/conversations?limit=${encodeURIComponent(limit)}`);
}

export async function deleteConversation(conversationId) {
  return requestJson(`${API_BASE}/conversations/${encodeURIComponent(conversationId)}`, {
    method: 'DELETE'
  });
}

export async function sendConversationMessage(conversationId, content, turnId = null) {
  return requestJson(`${API_BASE}/conversations/${conversationId}/messages`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content, ...(turnId ? { turn_id: turnId } : {}) })
  });
}

export async function getConversationMessages(conversationId) {
  return requestJson(`${API_BASE}/conversations/${conversationId}/messages`);
}

export async function getRunEvents(runId, afterEventId = null, limit = 1000) {
  const params = new window.URLSearchParams({ limit: String(limit) });
  if (afterEventId) params.set('after_event_id', afterEventId);
  return requestJson(`${API_BASE}/runs/${encodeURIComponent(runId)}/events?${params}`);
}

export async function startRun(prompt, flowId = 'default_flow') {
  return requestJson(`${API_BASE}/runs/start`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      flow_id: flowId,
      input_message: prompt,
      metadata: {
        use_llm: true
      }
    })
  });
}


export async function sendChatMessage(runId, message) {
  return requestJson(`${API_BASE}/runs/${runId}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message })
  });
}

export async function approveRun(runId) {
  return requestJson(`${API_BASE}/runs/${runId}/approve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ approved: true })
  });
}

function subscribeAuthenticatedSSE(url, onMessage, onError, extraHeaders = {}) {
  const controller = new window.AbortController();
  let closed = false;

  const consume = async () => {
    const requestGeneration = authSessionGeneration;
    const token = localStorage.getItem('agentflow_access_token');
    try {
      const response = await fetch(url, {
        headers: withAuthHeaders({ Accept: 'text/event-stream', ...extraHeaders }, token),
        cache: 'no-store',
        signal: controller.signal
      });

      if (!response.ok) {
        if (response.status === 401) expireSessionIfCurrent(token, requestGeneration);
        const detail = await response.text();
        throw new Error(`${response.status}: ${detail || response.statusText}`);
      }
      if (!response.body) throw new Error('The browser does not support streaming responses.');

      const reader = response.body.getReader();
      const decoder = new window.TextDecoder();
      let buffer = '';

      const handleFrame = (frame) => {
        const data = frame
          .split(/\r?\n/)
          .filter((line) => line.startsWith('data:'))
          .map((line) => line.slice(5).replace(/^ /, ''))
          .join('\n');
        if (!data) return;
        if (onMessage) onMessage(JSON.parse(data));
      };

      while (!closed) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const frames = buffer.split(/\r?\n\r?\n/);
        buffer = frames.pop() || '';
        frames.forEach(handleFrame);
      }

      buffer += decoder.decode();
      if (buffer.trim()) handleFrame(buffer);
      if (!closed && onError) onError(new Error('SSE connection closed before it was unsubscribed.'));
    } catch (error) {
      if (!closed && error.name !== 'AbortError' && onError) onError(error);
    }
  };

  consume();
  return () => {
    closed = true;
    controller.abort();
  };
}

export function subscribeRunSSEStream(runId, onMessage, onError, lastEventId = null) {
  const headers = lastEventId ? { 'Last-Event-ID': lastEventId } : {};
  return subscribeAuthenticatedSSE(
    `${API_BASE}/runs/${encodeURIComponent(runId)}/stream`,
    onMessage,
    onError,
    headers
  );
}

export function subscribeConversationEvents(conversationId, turnId, onMessage, onError) {
  const url = `${API_BASE}/conversations/${conversationId}/events?turn_id=${encodeURIComponent(turnId)}`;
  return subscribeAuthenticatedSSE(url, onMessage, onError);
}
