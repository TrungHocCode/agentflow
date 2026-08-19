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

export async function startRun(prompt, modelName = 'qwen3:8b') {
  const res = await fetch(`${API_BASE}/runs/start`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      user_prompt: prompt,
      model_name: modelName,
      use_llm: true
    })
  });
  if (!res.ok) throw new Error('Failed to start workflow run');
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
