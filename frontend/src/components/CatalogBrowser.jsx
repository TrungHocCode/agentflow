import React, { useState } from 'react';
import { Layers, Wrench, Search, Code, Globe, FileText, Database, Mail } from 'lucide-react';

export default function CatalogBrowser({ tools, agents }) {
  const [searchTerm, setSearchTerm] = useState('');
  const [activeTab, setActiveTab] = useState('tools');

  const filteredTools = tools.filter(tool => [
    tool.name,
    tool.description,
    tool.unavailable_reason
  ].some(value => String(value || '').toLowerCase().includes(searchTerm.toLowerCase())));

  const getToolIcon = (name) => {
    if (name.includes('crawler') || name.includes('http')) return Globe;
    if (name.includes('python')) return Code;
    if (name.includes('report') || name.includes('file')) return FileText;
    if (name.includes('database')) return Database;
    if (name.includes('email')) return Mail;
    return Wrench;
  };

  return (
    <div style={{ flex: 1, padding: '1.5rem', display: 'flex', flexDirection: 'column', gap: '1.25rem', overflowY: 'auto' }}>
      {/* Search Header Banner */}
      <div className="glass-panel" style={{ padding: '1.25rem', display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '1rem' }}>
        <div>
          <h2 style={{ fontSize: '1.125rem', color: '#fff' }}>Công cụ & trợ lý</h2>
          <p style={{ fontSize: '0.8125rem', color: 'var(--text-muted)' }}>Các khả năng có thể được sử dụng trong quá trình nghiên cứu.</p>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
          {/* Search Box */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', background: 'rgba(30,41,59,0.7)', border: '1px solid var(--glass-border)', padding: '0.5rem 0.875rem', borderRadius: 'var(--radius-md)' }}>
            <Search style={{ width: '16px', height: '16px', color: 'var(--text-muted)' }} />
            <input 
              type="text" 
              placeholder="Tìm công cụ hoặc trợ lý..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              style={{ background: 'transparent', border: 'none', color: '#fff', fontSize: '0.8125rem', outline: 'none' }}
            />
          </div>

          {/* Catalog Tab Toggle */}
          <div style={{ display: 'flex', background: 'rgba(30,41,59,0.8)', padding: '0.25rem', borderRadius: 'var(--radius-md)', border: '1px solid var(--glass-border)' }}>
            <button 
              onClick={() => setActiveTab('tools')}
              style={{ padding: '0.375rem 0.75rem', borderRadius: '6px', border: 'none', background: activeTab === 'tools' ? 'var(--gradient-brand)' : 'transparent', color: '#fff', fontSize: '0.8125rem', fontWeight: '600', cursor: 'pointer' }}
            >
              Công cụ ({tools.length})
            </button>
            <button 
              onClick={() => setActiveTab('agents')}
              style={{ padding: '0.375rem 0.75rem', borderRadius: '6px', border: 'none', background: activeTab === 'agents' ? 'var(--gradient-brand)' : 'transparent', color: '#fff', fontSize: '0.8125rem', fontWeight: '600', cursor: 'pointer' }}
            >
              Trợ lý ({agents.length})
            </button>
          </div>
        </div>
      </div>

      {/* Grid List */}
      {activeTab === 'tools' ? (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: '1rem' }}>
          {filteredTools.map((tool, idx) => {
            const ToolIcon = getToolIcon(tool.name);
            const isAvailable = tool.is_available !== false;
            return (
              <div key={idx} className="glass-card" style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem', padding: '1.25rem' }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.625rem' }}>
                    <div style={{ padding: '0.5rem', borderRadius: '8px', background: 'rgba(6,182,212,0.15)', border: '1px solid rgba(6,182,212,0.3)' }}>
                      <ToolIcon style={{ width: '20px', height: '20px', color: 'var(--accent-cyan)' }} />
                    </div>
                    <span style={{ fontSize: '0.9375rem', fontWeight: '700', color: '#fff' }}>{tool.name}</span>
                  </div>
                  <span
                    className={`badge ${isAvailable ? 'badge-done' : 'badge-failed'}`}
                    title={tool.unavailable_reason || undefined}
                  >
                    {isAvailable ? 'Sẵn sàng' : 'Tạm tắt'}
                  </span>
                </div>

                <p style={{ fontSize: '0.8125rem', color: 'var(--text-secondary)', lineHeight: 1.4, flex: 1 }}>
                  {tool.description}
                </p>
                {!isAvailable && tool.unavailable_reason && (
                  <p style={{ fontSize: '0.75rem', color: '#fca5a5', lineHeight: 1.4 }}>
                    {tool.unavailable_reason}
                  </p>
                )}

              </div>
            );
          })}
        </div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: '1rem' }}>
          {agents.map((agent, idx) => {
            const availableToolNames = agent.available_tool_names || agent.tool_names || [];
            const blockedToolNames = agent.blocked_tool_names || [];
            return (
              <div key={idx} className="glass-card" style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem', padding: '1.25rem' }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.625rem' }}>
                    <div style={{ padding: '0.5rem', borderRadius: '8px', background: 'rgba(99,102,241,0.15)', border: '1px solid rgba(99,102,241,0.3)' }}>
                      <Layers style={{ width: '20px', height: '20px', color: 'var(--accent-indigo)' }} />
                    </div>
                    <span style={{ fontSize: '0.9375rem', fontWeight: '700', color: '#fff' }}>{agent.name}</span>
                  </div>
                  <span className="badge badge-running">Sẵn sàng</span>
                </div>

                <p style={{ fontSize: '0.8125rem', color: 'var(--text-secondary)' }}>
                  Công cụ đang dùng được ({availableToolNames.length}):
                </p>

                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.375rem' }}>
                  {availableToolNames.map((toolName, toolIndex) => (
                    <span key={toolIndex} style={{ fontSize: '0.6875rem', background: 'rgba(6,182,212,0.15)', color: '#38bdf8', padding: '0.125rem 0.5rem', borderRadius: '4px', border: '1px solid rgba(6,182,212,0.3)' }}>
                      {toolName}
                    </span>
                  ))}
                  {blockedToolNames.map((toolName, toolIndex) => (
                    <span key={`blocked-${toolIndex}`} title="Bị tắt theo chính sách triển khai" style={{ fontSize: '0.6875rem', background: 'rgba(127,29,29,0.22)', color: '#fca5a5', padding: '0.125rem 0.5rem', borderRadius: '4px', border: '1px solid rgba(248,113,113,0.3)' }}>
                      {toolName} · tạm tắt
                    </span>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
