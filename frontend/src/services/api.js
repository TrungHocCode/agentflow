/**
 * AgentFlow API Client Service
 * Connects Frontend UI to FastAPI Backend REST API & Realtime SSE Stream.
 */

const API_BASE = '/api/v1';
const AUTH_EXPIRED_EVENT = 'agentflow:auth-expired';

function clearStoredSession() {
  localStorage.removeItem('agentflow_access_token');
  localStorage.removeItem('agentflow_user');
  window.dispatchEvent(new window.Event(AUTH_EXPIRED_EVENT));
}

function withAuthHeaders(headers = {}) {
  const token = localStorage.getItem('agentflow_access_token');
  return {
    ...headers,
    ...(token ? { Authorization: `Bearer ${token}` } : {})
  };
}

async function requestJson(url, options = {}) {
  const hadToken = Boolean(localStorage.getItem('agentflow_access_token'));
  const headers = withAuthHeaders(options.headers);
  const res = await fetch(url, { ...options, headers });
  if (res.status === 401 && hadToken) clearStoredSession();
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`${res.status}: ${detail || res.statusText}`);
  }
  return res.json();
}

export async function login(email, password) {
  const response = await requestJson(`${API_BASE}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password })
  });
  localStorage.setItem('agentflow_access_token', response.access_token);
  return response.user;
}

export async function register(email, password, displayName) {
  const response = await requestJson(`${API_BASE}/auth/register`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password, display_name: displayName })
  });
  localStorage.setItem('agentflow_access_token', response.access_token);
  return response.user;
}

export async function logout() {
  try {
    await requestJson(`${API_BASE}/auth/logout`, { method: 'POST' });
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

export async function retryRun(runId) {
  return requestJson(`${API_BASE}/runs/${runId}/retry`, { method: 'POST' });
}

export async function createWorkflowRun(workflowId, inputData = {}, metadata = {}) {
  return requestJson(`${API_BASE}/workflows/${workflowId}/runs`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Idempotency-Key': crypto.randomUUID()
    },
    body: JSON.stringify({ input_data: inputData, metadata })
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

export async function sendConversationMessage(conversationId, content) {
  return requestJson(`${API_BASE}/conversations/${conversationId}/messages`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content })
  });
}

export async function getRunEvents(runId, afterEventId = null) {
  const query = afterEventId
    ? `?after_event_id=${encodeURIComponent(afterEventId)}`
    : '';
  return requestJson(`${API_BASE}/runs/${runId}/events${query}`);
}

export async function startRun(prompt, modelName = 'qwen3:8b', flowId = 'default_flow') {
  return requestJson(`${API_BASE}/runs/start`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      flow_id: flowId,
      input_message: prompt,
      metadata: {
        model_name: modelName,
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

function subscribeAuthenticatedSSE(url, onMessage, onError) {
  const controller = new window.AbortController();
  let closed = false;

  const consume = async () => {
    try {
      const response = await fetch(url, {
        headers: withAuthHeaders({ Accept: 'text/event-stream' }),
        cache: 'no-store',
        signal: controller.signal
      });

      if (!response.ok) {
        if (response.status === 401) clearStoredSession();
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

export function subscribeRunSSEStream(runId, onMessage, onError) {
  return subscribeAuthenticatedSSE(`${API_BASE}/runs/${runId}/stream`, onMessage, onError);
}

export function subscribeConversationEvents(conversationId, turnId, onMessage, onError) {
  const url = `${API_BASE}/conversations/${conversationId}/events?turn_id=${encodeURIComponent(turnId)}`;
  return subscribeAuthenticatedSSE(url, onMessage, onError);
}
