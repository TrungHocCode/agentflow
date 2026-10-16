import { useEffect, useState } from 'react';
import { getCatalogAgents, getCatalogTools } from '../services/api';

// Shown when the backend catalog is unreachable so the assistant UI still renders.
const FALLBACK_TOOLS = [
  { name: 'news_crawler', description: 'Extracts article content and renders JavaScript pages when needed.' },
  { name: 'text_summarizer', description: 'Extracts concise bullet points from text.' },
  { name: 'markdown_report_generator', description: 'Compiles formatted markdown reports to workspace_data/reports/.' },
  { name: 'python_executor', description: 'Executes Python code in a safe subprocess.' },
  { name: 'database_query', description: 'Queries SQLite database tables.' },
  { name: 'email_sender', description: 'Sends mock emails to recipients.' },
  { name: 'http_request', description: 'Sends HTTP GET/POST requests.' },
  { name: 'web_search', description: 'Searches the web for relevant context.' }
];

const FALLBACK_AGENTS = [
  { name: 'news_crawler', tool_names: ['news_crawler', 'web_search', 'http_request'] },
  { name: 'text_summarizer', tool_names: ['text_summarizer', 'file_reader'] },
  { name: 'markdown_report_generator', tool_names: ['markdown_report_generator', 'file_writer'] }
];

export function useCatalogs() {
  const [tools, setTools] = useState([]);
  const [agents, setAgents] = useState([]);
  const [backendStatus, setBackendStatus] = useState(false);

  useEffect(() => {
    let cancelled = false;
    async function loadCatalogs() {
      try {
        const [toolsData, agentsData] = await Promise.all([getCatalogTools(), getCatalogAgents()]);
        if (cancelled) return;
        setTools(toolsData || []);
        setAgents(agentsData || []);
        setBackendStatus(true);
      } catch (err) {
        if (cancelled) return;
        console.warn('Backend API connection warning:', err);
        setBackendStatus(false);
        setTools(FALLBACK_TOOLS);
        setAgents(FALLBACK_AGENTS);
      }
    }
    loadCatalogs();
    return () => { cancelled = true; };
  }, []);

  return { tools, agents, backendStatus };
}
