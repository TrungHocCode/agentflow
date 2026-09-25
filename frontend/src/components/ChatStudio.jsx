import { useState, useEffect } from 'react';
import { Send, Bot, User, CheckCircle2, Play, Trash2, XCircle, AlertCircle, ArrowRight, Clock, LoaderCircle, FileText, ExternalLink } from 'lucide-react';
import StructuredText from './StructuredText';
import WorkflowProgressGraph from './WorkflowProgressGraph';

const RUN_STATUS = {
  queued: { label: 'Đang chờ', color: 'var(--accent-amber)' },
  running: { label: 'Đang thực thi', color: 'var(--accent-cyan)' },
  completed: { label: 'Đã hoàn tất', color: 'var(--accent-emerald)' },
  failed: { label: 'Thực thi thất bại', color: 'var(--accent-rose)' },
  interrupted: { label: 'Bị gián đoạn', color: 'var(--accent-rose)' },
  cancelled: { label: 'Đã hủy', color: 'var(--text-muted)' },
  abandoned: { label: 'Đã dừng', color: 'var(--text-muted)' }
};

const TERMINAL_RUN_STATUSES = new Set(['completed', 'failed', 'interrupted', 'cancelled', 'abandoned']);

function getRunStatusTone(status) {
  if (status === 'completed') return 'done';
  if (['failed', 'interrupted'].includes(status)) return 'failed';
  if (!status || TERMINAL_RUN_STATUSES.has(status)) return 'pending';
  return 'running';
}

export default function ChatStudio({
  messages,
  onSendMessage,
  onApprovePlan,
  onOpenResults,
  onViewRunDetails,
  onCancelRun,
  onDeleteConversation,
  conversationId,
  isCancelling,
  isDeletingConversation,
  isRestoring,
  isProcessing,
  isStreaming,
  activePlan,
  runStatus,
  runId
}) {
  const [inputPrompt, setInputPrompt] = useState('');
  const [liveThinkingSeconds, setLiveThinkingSeconds] = useState(0);

  // Live timer while AI is thinking
  useEffect(() => {
    let timer;
    if (isProcessing) {
      setLiveThinkingSeconds(0);
      const start = Date.now();
      timer = setInterval(() => {
        setLiveThinkingSeconds(((Date.now() - start) / 1000).toFixed(1));
      }, 100);
    } else {
      setLiveThinkingSeconds(0);
    }
    return () => clearInterval(timer);
  }, [isProcessing]);

  const promptSuggestions = [
    "Cào tin tức từ https://news.ycombinator.com, tóm tắt và sinh báo cáo Markdown.",
    "Viết mã Python tính 20 số Fibonacci đầu tiên và lưu kết quả vào file.",
    "Tìm kiếm thông tin về LLM Autonomous Agents và tổng hợp báo cáo."
  ];

  const handleSubmit = (e) => {
    e.preventDefault();
    if (!inputPrompt.trim() || isProcessing || isRestoring) return;
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
            <h2 style={{ fontSize: '1rem', color: 'var(--text-primary)' }}>Trợ lý nghiên cứu</h2>
            <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>Tìm hiểu thông tin, tổng hợp và tạo báo cáo theo yêu cầu của bạn.</p>
          </div>
        </div>
        {conversationId && (
          <button
            className="btn-secondary"
            onClick={onDeleteConversation}
            disabled={isDeletingConversation || isRestoring || (isProcessing && !isStreaming)}
            title={isRestoring ? 'Đang khôi phục hội thoại.' : isProcessing && !isStreaming ? 'Hãy đợi kế hoạch hoàn tất trước khi xóa.' : 'Xóa hội thoại'}
            style={{ color: 'var(--accent-rose)', gap: '0.4rem', whiteSpace: 'nowrap' }}
          >
            <Trash2 size={15} />
            {isDeletingConversation ? 'Đang xóa…' : 'Xóa hội thoại'}
          </button>
        )}
      </div>

      {/* Chat Messages Thread */}
      <div className="glass-panel" style={{ flex: 1, padding: '1.25rem', overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>
        {isRestoring && messages.length === 0 ? (
          <div role="status" style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: '0.75rem', color: 'var(--text-secondary)' }}>
            <LoaderCircle className="spin-slow" size={24} color="var(--accent-cyan)" />
            <p>Đang khôi phục hội thoại và kiểm tra tiến độ quy trình…</p>
          </div>
        ) : messages.length === 0 ? (
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', textAlign: 'center', padding: '2rem' }}>
            <div style={{ width: '64px', height: '64px', borderRadius: '20px', background: 'var(--gradient-brand)', display: 'flex', alignItems: 'center', justifyContent: 'center', marginBottom: '1rem', boxShadow: '0 0 30px rgba(99,102,241,0.4)' }}>
              <Bot style={{ width: '32px', height: '32px', color: '#fff' }} />
            </div>
            <h3 style={{ fontSize: '1.25rem', marginBottom: '0.5rem' }} className="text-gradient">Chào bạn! Tôi là trợ lý nghiên cứu</h3>
            <p style={{ fontSize: '0.875rem', color: 'var(--text-secondary)', maxWidth: '500px', marginBottom: '1.5rem' }}>
              Hãy mô tả điều bạn muốn tìm hiểu. Bạn có thể xem lại kế hoạch trước khi bắt đầu và theo dõi tiến độ ngay tại đây.
            </p>

            {/* Quick Suggestion Chips */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem', width: '100%', maxWidth: '560px' }}>
              <p style={{ fontSize: '0.75rem', fontWeight: '600', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Gợi ý nhanh:</p>
              {promptSuggestions.map((sug, idx) => (
                <button
                  key={idx}
                  disabled={isRestoring}
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
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '1rem', marginBottom: '0.25rem' }}>
                  <span style={{ fontSize: '0.75rem', color: msg.sender === 'user' ? '#e0e7ff' : 'var(--accent-cyan)', fontWeight: '600' }}>
                    {msg.sender === 'user' ? 'Bạn' : 'Trợ lý nghiên cứu'}
                  </span>
                  {msg.duration && (
                    <span style={{ fontSize: '0.7rem', color: 'rgba(255,255,255,0.6)', background: 'rgba(0,0,0,0.25)', padding: '2px 6px', borderRadius: '4px', display: 'flex', alignItems: 'center', gap: '3px' }}>
                      <Clock style={{ width: '10px', height: '10px', color: 'var(--accent-cyan)' }} />
                      {msg.duration}s
                    </span>
                  )}
                </div>
                <div style={{ fontSize: '0.875rem', lineHeight: 1.5 }}>
                  <StructuredText text={msg.text} />
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

        {/* Typing 3-Dots & Live Thinking Timer Indicator */}
        {isProcessing && !isStreaming && (
          <div style={{ display: 'flex', gap: '0.875rem', alignSelf: 'flex-start', margin: '0.5rem 0' }}>
            <div style={{ width: '32px', height: '32px', borderRadius: '50%', background: 'linear-gradient(135deg, #06b6d4, #6366f1)', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
              <Bot style={{ width: '18px', height: '18px', color: '#fff' }} />
            </div>
            <div className="glass-card" style={{ padding: '0.75rem 1.125rem', display: 'flex', alignItems: 'center', gap: '0.75rem', borderRadius: '16px 16px 16px 4px', border: '1px solid rgba(6,182,212,0.3)' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.35rem' }}>
                <span style={{ fontSize: '0.8125rem', color: 'var(--text-primary)', fontWeight: '600' }}>{isStreaming ? 'Đang chạy quy trình' : 'Đang xử lý yêu cầu'}</span>
                <span style={{ fontSize: '0.75rem', color: 'var(--accent-cyan)', background: 'rgba(6,182,212,0.15)', padding: '2px 6px', borderRadius: '4px', fontWeight: '600' }}>
                  ⏱️ {liveThinkingSeconds}s
                </span>
              </div>
              <div style={{ display: 'flex', gap: '5px', alignItems: 'center' }}>
                <span className="typing-dot"></span>
                <span className="typing-dot"></span>
                <span className="typing-dot"></span>
              </div>
            </div>
          </div>
        )}


        {/* Review and run the proposed workflow */}
        {activePlan && activePlan.length > 0 && (
          <div className="glass-panel" style={{ background: 'linear-gradient(135deg, rgba(99,102,241,0.1), rgba(6,182,212,0.08))', border: '1px solid rgba(99,102,241,0.4)', padding: '1.25rem', marginTop: '0.5rem' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '0.875rem' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                {runStatus === 'running' || runStatus === 'queued'
                  ? <LoaderCircle className="spin-slow" style={{ width: '20px', height: '20px', color: 'var(--accent-cyan)' }} />
                  : runStatus === 'failed' || runStatus === 'interrupted'
                    ? <AlertCircle style={{ width: '20px', height: '20px', color: 'var(--accent-rose)' }} />
                    : runStatus === 'completed'
                      ? <CheckCircle2 style={{ width: '20px', height: '20px', color: 'var(--accent-emerald)' }} />
                      : runStatus
                        ? <XCircle style={{ width: '20px', height: '20px', color: 'var(--text-muted)' }} />
                        : <CheckCircle2 style={{ width: '20px', height: '20px', color: 'var(--accent-emerald)' }} />}
                <h4 style={{ fontSize: '0.9375rem', color: '#fff' }}>{runStatus ? 'Quy trình của hội thoại này' : 'Kế hoạch đề xuất'}</h4>
              </div>
              <span
                className={`badge badge-${getRunStatusTone(runStatus)}`}
                style={runStatus && RUN_STATUS[runStatus] ? { color: RUN_STATUS[runStatus].color } : undefined}
              >
                {runStatus ? (RUN_STATUS[runStatus]?.label || 'Đang cập nhật') : 'Chờ bạn duyệt'}
              </span>
            </div>

            <WorkflowProgressGraph tasks={activePlan} runStatus={runStatus} />

            {!runStatus ? (
              <button className="btn-primary" onClick={onApprovePlan} disabled={isProcessing} style={{ width: '100%', justifyContent: 'center', padding: '0.75rem' }}>
                <Play style={{ width: '16px', height: '16px' }} />
                Duyệt và bắt đầu quy trình
              </button>
            ) : (
              <div style={{ display: 'flex', gap: '0.65rem' }}>
                <button className="btn-primary" onClick={onOpenResults} style={{ flex: 1, justifyContent: 'center', padding: '0.75rem' }}>
                  <FileText size={16} />
                  {runStatus === 'completed' ? 'Mở kết quả và tải tệp' : 'Mở kết quả'}
                </button>
                {runId && (
                  <button className="btn-secondary" onClick={onViewRunDetails} style={{ justifyContent: 'center', gap: '0.4rem', padding: '0.75rem' }}>
                    <ExternalLink size={15} /> Tiến độ
                  </button>
                )}
                {['queued', 'running'].includes(runStatus) && (
                  <button
                    className="btn-secondary"
                    onClick={onCancelRun}
                    disabled={isCancelling}
                    style={{ justifyContent: 'center', gap: '0.4rem', padding: '0.75rem', color: 'var(--accent-rose)' }}
                  >
                    <XCircle size={15} /> {isCancelling ? 'Đang hủy…' : 'Hủy'}
                  </button>
                )}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Input Chat Box */}
      <form onSubmit={handleSubmit} style={{ display: 'flex', gap: '0.75rem' }}>
        <input 
          type="text"
          value={inputPrompt}
          onChange={(e) => setInputPrompt(e.target.value)}
          placeholder="Bạn muốn tìm hiểu hoặc tổng hợp điều gì?"
          disabled={isProcessing || isRestoring}
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
        <button type="submit" className="btn-primary" disabled={isProcessing || isRestoring || !inputPrompt.trim()}>
          <Send style={{ width: '16px', height: '16px' }} />
          Gửi
        </button>
      </form>
    </div>
  );
}
