import { useEffect, useState } from 'react';
import { AlertCircle, Download, ExternalLink, FileText, Image, X } from 'lucide-react';
import { downloadRunArtifact, getRunArtifacts } from '../services/api';
import StructuredText from './StructuredText';
import { Alert, AlertDescription } from './ui/alert';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Card } from './ui/card';
import { Spinner } from './ui/spinner';

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
    <div className="results-overlay" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section
        role="dialog"
        aria-modal="true"
        aria-labelledby="run-results-title"
        className="results-drawer"
      >
        <header className="results-drawer__header">
          <div className="results-drawer__title-group">
            <p className="page-eyebrow">{selectedIsReport ? 'BÁO CÁO CUỐI' : hasReportArtifact ? 'BIỂU ĐỒ & TỆP' : 'KẾT QUẢ THEO BƯỚC'}</p>
            <h2 id="run-results-title" className="results-drawer__title">Kết quả nghiên cứu</h2>
            <Badge variant={runStatus === 'failed' ? 'destructive' : 'secondary'}>{statusLabel}</Badge>
          </div>
          <div className="results-drawer__actions">
            {results?.length > 0 && (
              <Button variant="outline" size="sm" onClick={handleDownloadStepOutputs}>
                <Download data-icon="inline-start" /> Tải kết quả từng bước
              </Button>
            )}
            {runId && (
              <Button variant="outline" size="sm" onClick={onViewRunDetails}>
                <ExternalLink data-icon="inline-start" /> Tiến độ
              </Button>
            )}
            <Button variant="ghost" size="icon" onClick={onClose} aria-label="Đóng kết quả"><X /></Button>
          </div>
        </header>

        {error && (
          <Alert variant="destructive" className="results-alert"><AlertCircle /><AlertDescription>{error}</AlertDescription></Alert>
        )}

        <div className={`results-drawer__body${artifacts.length > 1 ? ' results-drawer__body--with-list' : ''}`}>
          {artifacts.length > 1 && (
            <nav aria-label="Tệp kết quả" className="results-file-list">
              {artifacts.map((artifact) => (
                <Button
                  variant="outline"
                  size="sm"
                  key={artifact.id}
                  onClick={() => { setSelectedArtifact(artifact); setError(''); }}
                  className="results-file-button"
                  data-selected={selectedArtifact?.id === artifact.id}
                >
                  <span className="results-file-button__name">
                    {artifact.content_type?.includes('image') || artifact.name?.endsWith('.svg') ? <Image size={15} /> : <FileText size={15} />}
                    {artifact.name}
                  </span>
                  <small>{Math.max(1, Math.ceil((artifact.size_bytes || 0) / 1024))} KB</small>
                </Button>
              ))}
            </nav>
          )}

          <div className="results-drawer__content">
            {artifacts.length === 0 && !loading && results?.length > 0 && (
              <Alert className="result-notice">
                <AlertCircle />
                <AlertDescription>
                Chưa tìm thấy tệp báo cáo đính kèm cho lượt chạy này. Nội dung bên dưới là kết quả của từng bước (có thể gồm dữ liệu nguồn, bản tóm tắt và thông báo tạo tệp), chưa chắc là một báo cáo cuối duy nhất.
                </AlertDescription>
              </Alert>
            )}
            {artifacts.length > 0 && !artifacts.some((artifact) => artifactPriority(artifact) === 0) && (
              <Alert className="result-notice">
                <AlertCircle />
                <AlertDescription>
                Đã tìm thấy artifact nhưng chưa có file Markdown. Các output theo từng bước có thể xem bên dưới.
                </AlertDescription>
              </Alert>
            )}
            {selectedArtifact && (
              <div className="results-selected-file">
                <div className="results-selected-file__name">
                  <FileText size={17} aria-hidden="true" />
                  <strong>{selectedArtifact.name}</strong>
                </div>
                <Button onClick={handleDownload} disabled={downloading} size="sm">
                  {downloading ? <Spinner data-icon="inline-start" /> : <Download data-icon="inline-start" />}
                  Tải tệp
                </Button>
              </div>
            )}

            <Card className="results-viewer">
              {loading && !artifacts.length ? (
                <p className="results-viewer__message"><Spinner /> Đang tải kết quả…</p>
              ) : preview?.kind === 'markdown' ? (
                <StructuredText text={preview.value} />
              ) : preview?.kind === 'text' ? (
                <pre className="results-viewer__plain-text">{preview.value}</pre>
              ) : preview?.kind === 'image' ? (
                <img src={preview.value} alt={selectedArtifact?.name || 'Biểu đồ workflow'} className="results-viewer__image" />
              ) : preview?.kind === 'unsupported' ? (
                <p className="results-viewer__message">Định dạng này chưa xem trước được. Bạn có thể tải tệp xuống để mở.</p>
              ) : selectedArtifact && error ? (
                <p className="results-viewer__message">Không mở được tệp xem trước. Bạn vẫn có thể tải tệp xuống để xem.</p>
              ) : artifacts.length > 0 ? (
                <p className="results-viewer__message">Đang tải nội dung tệp…</p>
              ) : results?.length ? (
                <div className="results-step-list">
                  {results.map((item, index) => {
                    const text = resultText(item);
                    return (
                      <article key={item?.task_id || index} className="step-result">
                        <div className="step-result-heading">
                          <h3>
                            Bước {item?.task_id || index + 1} · {item?.description || 'Nội dung bước'}
                          </h3>
                          <Badge variant={item?.status === 'failed' ? 'destructive' : 'secondary'}>{item?.status === 'partial' ? 'Hoàn tất một phần' : (item?.status || 'done')}</Badge>
                        </div>
                        <StructuredText text={text} />
                      </article>
                    );
                  })}
                </div>
              ) : runStatus === 'failed' ? (
                <p className="results-viewer__message">Quy trình gặp sự cố trước khi tạo được kết quả. Bạn có thể kiểm tra bước thất bại hoặc thử lại.</p>
              ) : (
                <p className="results-viewer__message">Quy trình đang chạy. Kết quả sẽ xuất hiện tại đây khi các bước hoàn tất.</p>
              )}
            </Card>
            {artifacts.length > 0 && results?.length > 0 && (
              <details className="step-results-details">
                <summary>Đầu ra từng bước ({results.length})</summary>
                <div className="results-step-list">
                  {results.map((item, index) => (
                    <article key={item?.task_id || index} className="step-result">
                      <div className="step-result-heading">
                        <h3>
                          Bước {item?.task_id || index + 1} · {item?.description || 'Nội dung bước'}
                        </h3>
                        <Badge variant={item?.status === 'failed' ? 'destructive' : 'secondary'}>{item?.status === 'partial' ? 'Hoàn tất một phần' : (item?.status || 'done')}</Badge>
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
