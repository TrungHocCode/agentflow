import React, { useState } from 'react';
import { Layers, Wrench, Search, Code, Globe, FileText, Database, Mail, Terminal, BookOpen } from 'lucide-react';

export default function CatalogBrowser({ tools, agents }) {
  const [searchTerm, setSearchTerm] = useState('');
  const [activeTab, setActiveTab] = useState('tools');

  const filteredTools = tools.filter(t => 
    t.name.toLowerCase().includes(searchTerm.toLowerCase()) || 
    t.description.toLowerCase().includes(searchTerm.toLowerCase())
  );

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
          <h2 style={{ fontSize: '1.125rem', color: '#fff' }}>Agent & Tool Catalog Registry</h2>
          <p style={{ fontSize: '0.8125rem', color: 'var(--text-muted)' }}>Danh mục các công cụ và Agent worker sẵn có trong hệ thống</p>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
          {/* Search Box */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', background: 'rgba(30,41,59,0.7)', border: '1px solid var(--glass-border)', padding: '0.5rem 0.875rem', borderRadius: 'var(--radius-md)' }}>
            <Search style={{ width: '16px', height: '16px', color: 'var(--text-muted)' }} />
            <input 
              type="text" 
              placeholder="Tìm kiếm tool hoặc agent..."
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
              Tools ({tools.length})
            </button>
            <button 
              onClick={() => setActiveTab('agents')}
              style={{ padding: '0.375rem 0.75rem', borderRadius: '6px', border: 'none', background: activeTab === 'agents' ? 'var(--gradient-brand)' : 'transparent', color: '#fff', fontSize: '0.8125rem', fontWeight: '600', cursor: 'pointer' }}
            >
              Worker Agents ({agents.length})
            </button>
          </div>
        </div>
      </div>

      {/* Grid List */}
      {activeTab === 'tools' ? (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: '1rem' }}>
          {filteredTools.map((tool, idx) => {
            const ToolIcon = getToolIcon(tool.name);
            return (
              <div key={idx} className="glass-card" style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem', padding: '1.25rem' }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.625rem' }}>
                    <div style={{ padding: '0.5rem', borderRadius: '8px', background: 'rgba(6,182,212,0.15)', border: '1px solid rgba(6,182,212,0.3)' }}>
                      <ToolIcon style={{ width: '20px', height: '20px', color: 'var(--accent-cyan)' }} />
                    </div>
                    <span style={{ fontSize: '0.9375rem', fontWeight: '700', color: '#fff' }}>{tool.name}</span>
                  </div>
                  <span className="badge badge-done">Registered</span>
                </div>

                <p style={{ fontSize: '0.8125rem', color: 'var(--text-secondary)', lineHeight: 1.4, flex: 1 }}>
                  {tool.description}
                </p>

                <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', paddingTop: '0.5rem', borderTop: '1px solid rgba(255,255,255,0.05)', display: 'flex', justifyContent: 'space-between' }}>
                  <span>Schema: LangChain BaseTool</span>
                  <span style={{ color: 'var(--accent-indigo)' }}>Double-Decorator Registered</span>
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: '1rem' }}>
          {agents.map((agent, idx) => (
            <div key={idx} className="glass-card" style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem', padding: '1.25rem' }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.625rem' }}>
                  <div style={{ padding: '0.5rem', borderRadius: '8px', background: 'rgba(99,102,241,0.15)', border: '1px solid rgba(99,102,241,0.3)' }}>
                    <Layers style={{ width: '20px', height: '20px', color: 'var(--accent-indigo)' }} />
                  </div>
                  <span style={{ fontSize: '0.9375rem', fontWeight: '700', color: '#fff' }}>{agent.name}</span>
                </div>
                <span className="badge badge-running">ReAct Agent</span>
              </div>

              <p style={{ fontSize: '0.8125rem', color: 'var(--text-secondary)' }}>
                Tools Whitelist Attached ({agent.tool_names ? agent.tool_names.length : 0}):
              </p>

              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.375rem' }}>
                {(agent.tool_names || []).map((tName, tIdx) => (
                  <span key={tIdx} style={{ fontSize: '0.6875rem', background: 'rgba(6,182,212,0.15)', color: '#38bdf8', padding: '0.125rem 0.5rem', borderRadius: '4px', border: '1px solid rgba(6,182,212,0.3)' }}>
                    {tName}
                  </span>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
