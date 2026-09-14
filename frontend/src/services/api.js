/**
 * AgentFlow API Client Service
 * Connects Frontend UI to FastAPI Backend REST API & Realtime SSE Stream.
 */

const API_BASE = '/api/v1';

async function requestJson(url, options = {}) {
  const token = localStorage.getItem('agentflow_access_token');
  const headers = {
    ...(options.headers || {}),
    ...(token ? { Authorization: `Bearer ${token}` } : {})
  };
  const res = await fetch(url, { ...options, headers });
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
  const res = await fetch(`${API_BASE}/flows`);
  if (!res.ok) throw new Error('Failed to fetch flows');
  return res.json();
}

export async function getRuns() {
  const res = await fetch(`${API_BASE}/runs`);
  if (!res.ok) throw new Error('Failed to fetch runs');
  return res.json();
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
  const res = await fetch(`${API_BASE}/runs/${runId}/events${query}`);
  if (!res.ok) throw new Error(`Failed to fetch events for ${runId}`);
  return res.json();
}

export async function startRun(prompt, modelName = 'qwen3:8b', flowId = 'default_flow') {
  const res = await fetch(`${API_BASE}/runs/start`, {
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
  if (!res.ok) {
    const errorText = await res.text();
    throw new Error(`Failed to start workflow run (${res.status}): ${errorText}`);
  }
  return res.json();
}


export async function sendChatMessage(runId, message) {
  const res = await fetch(`${API_BASE}/runs/${runId}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message })
  });
  if (!res.ok) throw new Error(`Failed to send message: ${res.statusText}`);
  return res.json();
}

export async function approveRun(runId) {
  const res = await fetch(`${API_BASE}/runs/${runId}/approve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ approved: true })
  });
  if (!res.ok) throw new Error('Failed to approve run');
  return res.json();
}

export function subscribeRunSSEStream(runId, onMessage, onError) {
  const url = `${API_BASE}/runs/${runId}/stream`;
  const eventSource = new EventSource(url);

  eventSource.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      if (onMessage) onMessage(data);
    } catch (e) {
      console.error("Error parsing SSE event data:", e);
    }
  };

  eventSource.onerror = (err) => {
    console.error("SSE stream error:", err);
    if (onError) onError(err);
    eventSource.close();
  };

  return () => {
    eventSource.close();
  };
}

export function subscribeConversationEvents(conversationId, turnId, onMessage, onError) {
  const url = `${API_BASE}/conversations/${conversationId}/events?turn_id=${encodeURIComponent(turnId)}`;
  const eventSource = new EventSource(url);

  eventSource.onmessage = (event) => {
    try {
      if (onMessage) onMessage(JSON.parse(event.data));
    } catch (error) {
      if (onError) onError(error);
    }
  };
  eventSource.onerror = (error) => {
    if (onError) onError(error);
    eventSource.close();
  };
  return () => eventSource.close();
}
