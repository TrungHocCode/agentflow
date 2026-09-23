import { useEffect, useState } from 'react';
import { AlertCircle, Download, ExternalLink, FileText, Image, LoaderCircle, X } from 'lucide-react';
import { downloadRunArtifact, getRunArtifacts } from '../services/api';
import StructuredText from './StructuredText';

function artifactPriority(artifact) {
  const name = String(artifact.name || '').toLowerCase();
  if (name.endsWith('.md') || name.endsWith('.markdown')) return 0;
  if (name.endsWith('.svg')) return 1;
  if (name.endsWith('.json')) return 2;
  return 3;
}

function resultText(item) {
  const value = item?.result ?? item?.content ?? item;
  return typeof value === 'string' ? value : JSON.stringify(value, null, 2);
}

function saveBlob(blob, filename) {
  const url = window.URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => window.URL.revokeObjectURL(url), 1000);
}

const PANEL_STYLE = {
  position: 'fixed',
  inset: 0,
  zIndex: 1000,
  display: 'flex',
  justifyContent: 'flex-end',
  background: 'rgba(16, 12, 10, 0.72)',
  backdropFilter: 'blur(5px)'
};

export default function RunResultsDrawer({
  open,
  onClose,
  runId,
  runStatus,
  results,
  onViewRunDetails
}) {
  const [artifacts, setArtifacts] = useState([]);
  const [selectedArtifact, setSelectedArtifact] = useState(null);
  const [preview, setPreview] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [downloading, setDownloading] = useState(false);

  useEffect(() => {
    if (!open) return undefined;
    if (!runId) {
      setArtifacts([]);
      setSelectedArtifact(null);
      setPreview(null);
      setError('');
      setLoading(false);
      return undefined;
    }
    let cancelled = false;
    setLoading(true);
    setError('');
    setArtifacts([]);
    setSelectedArtifact(null);
    getRunArtifacts(runId)
      .then((data) => {
        if (cancelled) return;
        const items = Array.isArray(data) ? [...data].sort((left, right) => artifactPriority(left) - artifactPriority(right)) : [];
        setArtifacts(items);
        setSelectedArtifact((current) => items.find((item) => item.id === current?.id) || items[0] || null);
      })
      .catch((loadError) => {
        if (!cancelled) setError(`Không thể tải danh sách tệp: ${loadError.message}`);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [open, runId, runStatus]);

  useEffect(() => {
    if (!open || !runId || !selectedArtifact?.id) {
      setPreview(null);
      return undefined;
    }
    let cancelled = false;
    let objectUrl = null;
    setPreview(null);
    downloadRunArtifact(runId, selectedArtifact.id)
      .then(async (blob) => {
        if (cancelled) return;
        const contentType = selectedArtifact.content_type || blob.type || '';
        const extension = selectedArtifact.name?.split('.').pop()?.toLowerCase() || '';
        if (contentType.includes('svg') || extension === 'svg') {
          objectUrl = window.URL.createObjectURL(blob);
          setPreview({ kind: 'image', value: objectUrl });
        } else if (
          contentType.startsWith('text/') ||
          /json|csv|xml|yaml/.test(contentType) ||
          ['md', 'markdown', 'txt', 'json', 'csv', 'log', 'yaml', 'yml', 'xml'].includes(extension)
        ) {
          let value = await blob.text();
          if (extension === 'json') {
            try {
              value = JSON.stringify(JSON.parse(value), null, 2);
            } catch {
              // Keep malformed or non-standard JSON visible as plain text.
            }
          }
          if (!cancelled) setPreview({ kind: extension === 'md' || extension === 'markdown' ? 'markdown' : 'text', value });
        } else {
          setPreview({ kind: 'unsupported', value: '' });
        }
      })
      .catch((loadError) => {
        if (!cancelled) setError(`Không thể mở tệp: ${loadError.message}`);
      });
    return () => {
      cancelled = true;
      if (objectUrl) window.URL.revokeObjectURL(objectUrl);
    };
  }, [open, runId, selectedArtifact]);

  useEffect(() => {
    if (!open) return undefined;
    const onKeyDown = (event) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [open, onClose]);

  if (!open) return null;

  const handleDownload = async () => {
    if (!runId || !selectedArtifact?.id) return;
    setDownloading(true);
    setError('');
    try {
      const blob = await downloadRunArtifact(runId, selectedArtifact.id);
      saveBlob(blob, selectedArtifact.name || 'agentflow-result');
    } catch (downloadError) {
      setError(`Không thể tải tệp: ${downloadError.message}`);
    } finally {
      setDownloading(false);
    }
  };

  const handleDownloadStepOutputs = () => {
    if (!results?.length) return;
    const report = [
      '# Kết quả nghiên cứu',
      '',
      '> Các kết quả dưới đây được tạo ở từng bước của quy trình.',
      '',
      ...results.flatMap((item, index) => [
        `## Bước ${item?.task_id || index + 1}: ${item?.description || 'Nội dung bước'}`,
        '',
        resultText(item),
        '',
        '---',
        ''
      ])
    ].join('\n');
    saveBlob(new window.Blob([report], { type: 'text/markdown;charset=utf-8' }), 'agentflow-research-results.md');
  };

  const statusLabel = {
    queued: 'Đang chờ',
    running: 'Đang chạy',
    completed: 'Hoàn tất',
    failed: 'Thất bại',
    interrupted: 'Bị gián đoạn',
    cancelled: 'Đã hủy'
  }[runStatus] || 'Đang cập nhật';
  const hasReportArtifact = artifacts.some((artifact) => artifactPriority(artifact) === 0);
  const selectedIsReport = selectedArtifact && artifactPriority(selectedArtifact) === 0;

  return (
    <div style={PANEL_STYLE} onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section
        role="dialog"
        aria-modal="true"
        aria-labelledby="run-results-title"
        className="glass-panel"
        style={{
          width: 'min(900px, 100%)',
          height: '100%',
          borderRadius: 'var(--radius-lg) 0 0 var(--radius-lg)',
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
          background: 'var(--bg-secondary)'
        }}
      >
        <header style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '1rem', padding: '1.1rem 1.3rem', borderBottom: '1px solid var(--glass-border)' }}>
          <div style={{ minWidth: 0 }}>
            <p className="eyebrow">{selectedIsReport ? 'BÁO CÁO CUỐI' : hasReportArtifact ? 'BIỂU ĐỒ & TỆP' : 'KẾT QUẢ THEO BƯỚC'} · {statusLabel}</p>
            <h2 id="run-results-title" style={{ color: 'var(--text-primary)', fontSize: '1.15rem' }}>Kết quả nghiên cứu</h2>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexShrink: 0 }}>
            {results?.length > 0 && (
              <button className="btn-secondary" onClick={handleDownloadStepOutputs} style={{ gap: '0.4rem' }}>
                <Download size={15} /> Tải kết quả từng bước
              </button>
            )}
            {runId && (
              <button className="btn-secondary" onClick={onViewRunDetails} style={{ gap: '0.4rem' }}>
                <ExternalLink size={15} /> Tiến độ
              </button>
            )}
            <button className="btn-secondary" onClick={onClose} aria-label="Đóng kết quả" style={{ padding: '0.55rem' }}>
              <X size={17} />
            </button>
          </div>
        </header>

        {error && (
          <div role="alert" style={{ margin: '0.9rem 1.2rem 0', color: 'var(--accent-rose)', display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: '0.82rem' }}>
            <AlertCircle size={16} /> {error}
          </div>
        )}

        <div style={{ display: 'grid', gridTemplateColumns: artifacts.length > 1 ? '230px minmax(0, 1fr)' : 'minmax(0, 1fr)', gap: '1rem', flex: 1, minHeight: 0, padding: '1rem 1.2rem 1.2rem' }}>
          {artifacts.length > 1 && (
            <nav aria-label="Tệp kết quả" style={{ overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
              {artifacts.map((artifact) => (
                <button
                  key={artifact.id}
                  onClick={() => { setSelectedArtifact(artifact); setError(''); }}
                  className="glass-card"
                  style={{ textAlign: 'left', color: 'var(--text-primary)', borderColor: selectedArtifact?.id === artifact.id ? 'var(--accent-cyan)' : 'var(--glass-border)', cursor: 'pointer' }}
                >
                  <span style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', overflowWrap: 'anywhere' }}>
                    {artifact.content_type?.includes('image') || artifact.name?.endsWith('.svg') ? <Image size={15} /> : <FileText size={15} />}
                    {artifact.name}
                  </span>
                  <small style={{ color: 'var(--text-muted)' }}>{Math.max(1, Math.ceil((artifact.size_bytes || 0) / 1024))} KB</small>
                </button>
              ))}
            </nav>
          )}

          <div style={{ minWidth: 0, minHeight: 0, display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
            {artifacts.length === 0 && !loading && results?.length > 0 && (
              <div className="result-notice">
                Chưa tìm thấy tệp báo cáo đính kèm cho lượt chạy này. Nội dung bên dưới là kết quả của từng bước (có thể gồm dữ liệu nguồn, bản tóm tắt và thông báo tạo tệp), chưa chắc là một báo cáo cuối duy nhất.
              </div>
            )}
            {artifacts.length > 0 && !artifacts.some((artifact) => artifactPriority(artifact) === 0) && (
              <div className="result-notice">
                Đã tìm thấy artifact nhưng chưa có file Markdown. Các output theo từng bước có thể xem bên dưới.
              </div>
            )}
            {selectedArtifact && (
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '0.75rem' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', minWidth: 0, color: 'var(--text-primary)' }}>
                  <FileText size={17} color="var(--accent-cyan)" />
                  <strong style={{ overflowWrap: 'anywhere' }}>{selectedArtifact.name}</strong>
                </div>
                <button className="btn-primary" onClick={handleDownload} disabled={downloading} style={{ flexShrink: 0, gap: '0.4rem' }}>
                  {downloading ? <LoaderCircle size={15} className="spin-slow" /> : <Download size={15} />}
                  Tải tệp
                </button>
              </div>
            )}

            <div className="glass-card" style={{ flex: 1, minHeight: 0, overflow: 'auto', color: 'var(--text-primary)' }}>
              {loading && !artifacts.length ? (
                <p style={{ color: 'var(--text-secondary)', display: 'flex', alignItems: 'center', gap: '0.5rem' }}><LoaderCircle size={16} className="spin-slow" /> Đang tải kết quả…</p>
              ) : preview?.kind === 'markdown' ? (
                <StructuredText text={preview.value} />
              ) : preview?.kind === 'text' ? (
                <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', font: '0.82rem/1.6 var(--font-mono)' }}>{preview.value}</pre>
              ) : preview?.kind === 'image' ? (
                <img src={preview.value} alt={selectedArtifact?.name || 'Biểu đồ workflow'} style={{ maxWidth: '100%', height: 'auto', display: 'block', margin: 'auto' }} />
              ) : preview?.kind === 'unsupported' ? (
                <p style={{ color: 'var(--text-secondary)' }}>Định dạng này chưa xem trước được. Bạn có thể tải tệp xuống để mở.</p>
              ) : selectedArtifact && error ? (
                <p style={{ color: 'var(--accent-rose)' }}>Không mở được tệp xem trước. Bạn vẫn có thể tải tệp xuống để xem.</p>
              ) : artifacts.length > 0 ? (
                <p style={{ color: 'var(--text-muted)' }}>Đang tải nội dung tệp…</p>
              ) : results?.length ? (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.9rem' }}>
                  {results.map((item, index) => {
                    const text = resultText(item);
                    return (
                      <article key={item?.task_id || index} className="step-result">
                        <div className="step-result-heading">
                          <h3 style={{ color: 'var(--accent-cyan)', fontSize: '0.9rem' }}>
                            Bước {item?.task_id || index + 1} · {item?.description || 'Nội dung bước'}
                          </h3>
                          <span className={`badge ${item?.status === 'failed' ? 'badge-failed' : 'badge-done'}`}>{item?.status || 'done'}</span>
                        </div>
                        <StructuredText text={text} />
                      </article>
                    );
                  })}
                </div>
              ) : runStatus === 'failed' ? (
                <p style={{ color: 'var(--accent-rose)' }}>Quy trình gặp sự cố trước khi tạo được kết quả. Bạn có thể kiểm tra bước thất bại hoặc thử lại.</p>
              ) : (
                <p style={{ color: 'var(--text-secondary)' }}>Quy trình đang chạy. Kết quả sẽ xuất hiện tại đây khi các bước hoàn tất.</p>
              )}
            </div>
            {artifacts.length > 0 && results?.length > 0 && (
              <details className="step-results-details">
                <summary>Đầu ra từng bước ({results.length})</summary>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem', marginTop: '0.75rem' }}>
                  {results.map((item, index) => (
                    <article key={item?.task_id || index} className="step-result">
                      <div className="step-result-heading">
                        <h3 style={{ color: 'var(--accent-cyan)', fontSize: '0.9rem' }}>
                          Bước {item?.task_id || index + 1} · {item?.description || 'Nội dung bước'}
                        </h3>
                        <span className={`badge ${item?.status === 'failed' ? 'badge-failed' : 'badge-done'}`}>{item?.status || 'done'}</span>
                      </div>
                      <StructuredText text={resultText(item)} />
                    </article>
                  ))}
                </div>
              </details>
            )}
          </div>
        </div>
      </section>
    </div>
  );
}
