/**
 * AgentFlow API Client Service
 * Connects Frontend UI to FastAPI Backend REST API & Realtime SSE Stream.
 */

const API_BASE = '/api/v1';

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
  const res = await fetch(`${API_BASE}/runs/${runId}`);
  if (!res.ok) throw new Error(`Failed to fetch run details for ${runId}`);
  return res.json();
}

export async function createWorkflowRun(workflowId, inputData = {}, metadata = {}) {
  const res = await fetch(`${API_BASE}/workflows/${workflowId}/runs`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Idempotency-Key': crypto.randomUUID()
    },
    body: JSON.stringify({ input_data: inputData, metadata })
  });
  if (!res.ok) throw new Error(`Failed to create workflow run (${res.status})`);
  return res.json();
}

export async function createConversation(title = '', workflowId = null) {
  const res = await fetch(`${API_BASE}/conversations`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title, workflow_id: workflowId })
  });
  if (!res.ok) throw new Error(`Failed to create conversation (${res.status})`);
  return res.json();
}

export async function sendConversationMessage(conversationId, content) {
  const res = await fetch(`${API_BASE}/conversations/${conversationId}/messages`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content })
  });
  if (!res.ok) throw new Error(`Failed to send conversation message (${res.status})`);
  return res.json();
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
