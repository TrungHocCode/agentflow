import React, { useState } from 'react';
import { Send, Bot, User, CheckCircle2, Play, Sparkles, AlertCircle, ArrowRight } from 'lucide-react';

export default function ChatStudio({ messages, onSendMessage, onApprovePlan, isProcessing, activePlan, selectedModel }) {
  const [inputPrompt, setInputPrompt] = useState('');

  const promptSuggestions = [
    "Cào tin tức từ https://news.ycombinator.com, tóm tắt và sinh báo cáo Markdown.",
    "Viết mã Python tính 20 số Fibonacci đầu tiên và lưu kết quả vào file.",
    "Tìm kiếm thông tin về LLM Autonomous Agents và tổng hợp báo cáo."
  ];

  const handleSubmit = (e) => {
    e.preventDefault();
    if (!inputPrompt.trim() || isProcessing) return;
    onSendMessage(inputPrompt);
    setInputPrompt('');
  };

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', height: '100%', padding: '1.5rem', gap: '1rem', overflow: 'hidden' }}>
      {/* Header Info Banner */}
      <div className="glass-panel" style={{ padding: '1rem 1.25rem', display: 'flex', alignItems: 'center', justifyContent: 'space-between', background: 'linear-gradient(135deg, rgba(15,23,42,0.8), rgba(30,41,59,0.5))' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
          <div style={{ padding: '0.5rem', borderRadius: 'var(--radius-md)', background: 'rgba(6,182,212,0.15)', border: '1px solid rgba(6,182,212,0.3)' }}>
            <Bot style={{ width: '22px', height: '22px', color: 'var(--accent-cyan)' }} />
          </div>
          <div>
            <h2 style={{ fontSize: '1rem', color: 'var(--text-primary)' }}>Supervisor Agent — Conversational Designer</h2>
            <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>Trò chuyện để làm rõ yêu cầu & nhận kế hoạch DAG trước khi thực thi với model {selectedModel}</p>
          </div>
        </div>
        <span className="badge badge-running">
          <Sparkles style={{ width: '12px', height: '12px' }} /> Two-Phase Active
        </span>
      </div>

      {/* Chat Messages Thread */}
      <div className="glass-panel" style={{ flex: 1, padding: '1.25rem', overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>
        {messages.length === 0 ? (
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', textAlign: 'center', padding: '2rem' }}>
            <div style={{ width: '64px', height: '64px', borderRadius: '20px', background: 'var(--gradient-brand)', display: 'flex', alignItems: 'center', justifyContent: 'center', marginBottom: '1rem', boxShadow: '0 0 30px rgba(99,102,241,0.4)' }}>
              <Bot style={{ width: '32px', height: '32px', color: '#fff' }} />
            </div>
            <h3 style={{ fontSize: '1.25rem', marginBottom: '0.5rem' }} className="text-gradient">Chào bạn! Tôi là Supervisor Agent</h3>
            <p style={{ fontSize: '0.875rem', color: 'var(--text-secondary)', maxWidth: '500px', marginBottom: '1.5rem' }}>
              Hãy nhập yêu cầu workflow bạn muốn tạo. Tôi sẽ trò chuyện làm rõ yêu cầu, tư vấn công cụ và đề xuất Kế hoạch DAG phù hợp trước khi chạy!
            </p>

            {/* Quick Suggestion Chips */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem', width: '100%', maxWidth: '560px' }}>
              <p style={{ fontSize: '0.75rem', fontWeight: '600', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Gợi ý nhanh:</p>
              {promptSuggestions.map((sug, idx) => (
                <button
                  key={idx}
                  onClick={() => setInputPrompt(sug)}
                  className="glass-card"
                  style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', textAlign: 'left', cursor: 'pointer', padding: '0.75rem 1rem', fontSize: '0.8125rem', color: 'var(--text-primary)' }}
                >
                  <span>{sug}</span>
                  <ArrowRight style={{ width: '14px', height: '14px', color: 'var(--accent-indigo)' }} />
                </button>
              ))}
            </div>
          </div>
        ) : (
          messages.map((msg, idx) => (
            <div 
              key={idx} 
              style={{ 
                display: 'flex', 
                gap: '0.875rem', 
                alignSelf: msg.sender === 'user' ? 'flex-end' : 'flex-start',
                maxWidth: '85%'
              }}
            >
              {msg.sender === 'supervisor' && (
                <div style={{ width: '32px', height: '32px', borderRadius: '50%', background: 'linear-gradient(135deg, #06b6d4, #6366f1)', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
                  <Bot style={{ width: '18px', height: '18px', color: '#fff' }} />
                </div>
              )}

              <div 
                style={{ 
                  background: msg.sender === 'user' ? 'linear-gradient(135deg, #6366f1, #8b5cf6)' : 'rgba(30,41,59,0.7)',
                  border: msg.sender === 'user' ? 'none' : '1px solid var(--glass-border)',
                  padding: '0.875rem 1.125rem',
                  borderRadius: msg.sender === 'user' ? '16px 16px 4px 16px' : '16px 16px 16px 4px',
                  color: '#fff',
                  boxShadow: msg.sender === 'user' ? '0 4px 14px rgba(99,102,241,0.3)' : 'none'
                }}
              >
                <div style={{ fontSize: '0.75rem', color: msg.sender === 'user' ? '#e0e7ff' : 'var(--accent-cyan)', fontWeight: '600', marginBottom: '0.25rem' }}>
                  {msg.sender === 'user' ? 'Bạn' : 'Supervisor Agent'}
                </div>
                <div style={{ fontSize: '0.875rem', whiteSpace: 'pre-wrap', lineHeight: 1.5 }}>
                  {msg.text}
                </div>
              </div>

              {msg.sender === 'user' && (
                <div style={{ width: '32px', height: '32px', borderRadius: '50%', background: 'rgba(51,65,85,0.8)', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
                  <User style={{ width: '18px', height: '18px', color: '#cbd5e1' }} />
                </div>
              )}
            </div>
          ))
        )}

        {/* Proposed DAG Plan Proposal Card */}
        {activePlan && activePlan.length > 0 && (
          <div className="glass-panel" style={{ background: 'linear-gradient(135deg, rgba(99,102,241,0.1), rgba(6,182,212,0.08))', border: '1px solid rgba(99,102,241,0.4)', padding: '1.25rem', marginTop: '0.5rem' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '0.875rem' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                <CheckCircle2 style={{ width: '20px', height: '20px', color: 'var(--accent-emerald)' }} />
                <h4 style={{ fontSize: '0.9375rem', color: '#fff' }}>Đề Xuất Kế Hoạch DAG Execution Plan</h4>
              </div>
              <span className="badge badge-pending">Awaiting Approval</span>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem', marginBottom: '1rem' }}>
              {activePlan.map((task) => (
                <div key={task.id} className="glass-card" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0.625rem 0.875rem' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                    <span style={{ width: '24px', height: '24px', borderRadius: '50%', background: 'rgba(99,102,241,0.3)', color: '#c7d2fe', fontSize: '0.75rem', fontWeight: '700', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                      {task.id}
                    </span>
                    <div>
                      <p style={{ fontSize: '0.8125rem', color: '#fff', fontWeight: '600' }}>{task.description}</p>
                      <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>Node Agent: <code style={{ color: 'var(--accent-cyan)' }}>{task.node}</code></p>
                    </div>
                  </div>
                  <span className="badge badge-pending">{task.status}</span>
                </div>
              ))}
            </div>

            <button className="btn-primary" onClick={onApprovePlan} disabled={isProcessing} style={{ width: '100%', justifyContent: 'center', padding: '0.75rem' }}>
              <Play style={{ width: '16px', height: '16px' }} />
              Duyệt & Bắt Đầu Thực Thi Workflow
            </button>
          </div>
        )}
      </div>

      {/* Input Chat Box */}
      <form onSubmit={handleSubmit} style={{ display: 'flex', gap: '0.75rem' }}>
        <input 
          type="text"
          value={inputPrompt}
          onChange={(e) => setInputPrompt(e.target.value)}
          placeholder="Nhập yêu cầu workflow hoặc trả lời Supervisor Agent..."
          disabled={isProcessing}
          style={{ 
            flex: 1,
            background: 'rgba(30,41,59,0.7)',
            border: '1px solid var(--glass-border)',
            borderRadius: 'var(--radius-md)',
            padding: '0.875rem 1.25rem',
            color: '#fff',
            fontSize: '0.875rem',
            outline: 'none',
            backdropFilter: 'blur(8px)'
          }}
        />
        <button type="submit" className="btn-primary" disabled={isProcessing || !inputPrompt.trim()}>
          <Send style={{ width: '16px', height: '16px' }} />
          Gửi
        </button>
      </form>
    </div>
  );
}
