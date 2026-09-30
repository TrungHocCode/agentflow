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
        if (!cancelled) setError(`Could not load the file list: ${loadError.message}`);
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
        if (!cancelled) setError(`Could not open the file: ${loadError.message}`);
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
      setError(`Could not download the file: ${downloadError.message}`);
    } finally {
      setDownloading(false);
    }
  };

  const handleDownloadStepOutputs = () => {
    if (!results?.length) return;
    const report = [
      '# Research Results',
      '',
      '> The following outputs were produced by individual workflow steps.',
      '',
      ...results.flatMap((item, index) => [
        `## Step ${item?.task_id || index + 1}: ${item?.description || 'Step output'}`,
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
    queued: 'Queued',
    running: 'Running',
    completed: 'Completed',
    failed: 'Failed',
    interrupted: 'Interrupted',
    cancelled: 'Cancelled'
  }[runStatus] || 'Updating';
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
            <p className="page-eyebrow">{selectedIsReport ? 'FINAL REPORT' : hasReportArtifact ? 'CHARTS & FILES' : 'STEP OUTPUTS'}</p>
            <h2 id="run-results-title" className="results-drawer__title">Research results</h2>
            <Badge variant={runStatus === 'failed' ? 'destructive' : 'secondary'}>{statusLabel}</Badge>
          </div>
          <div className="results-drawer__actions">
            {results?.length > 0 && (
              <Button variant="outline" size="sm" onClick={handleDownloadStepOutputs}>
                <Download data-icon="inline-start" /> Download step outputs
              </Button>
            )}
            {runId && (
              <Button variant="outline" size="sm" onClick={onViewRunDetails}>
                <ExternalLink data-icon="inline-start" /> Progress
              </Button>
            )}
            <Button variant="ghost" size="icon" onClick={onClose} aria-label="Close results"><X /></Button>
          </div>
        </header>

        {error && (
          <Alert variant="destructive" className="results-alert"><AlertCircle /><AlertDescription>{error}</AlertDescription></Alert>
        )}

        <div className={`results-drawer__body${artifacts.length > 1 ? ' results-drawer__body--with-list' : ''}`}>
          {artifacts.length > 1 && (
            <nav aria-label="Result files" className="results-file-list">
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
                No report file is attached to this run. The content below contains individual step outputs (which may include source data, summaries, or file-generation messages), not necessarily one consolidated final report.
                </AlertDescription>
              </Alert>
            )}
            {artifacts.length > 0 && !artifacts.some((artifact) => artifactPriority(artifact) === 0) && (
              <Alert className="result-notice">
                <AlertCircle />
                <AlertDescription>
                An artifact was found, but there is no Markdown report. Individual step outputs are available below.
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
                  Download file
                </Button>
              </div>
            )}

            <Card className="results-viewer">
              {loading && !artifacts.length ? (
                <p className="results-viewer__message"><Spinner /> Loading results…</p>
              ) : preview?.kind === 'markdown' ? (
                <StructuredText text={preview.value} />
              ) : preview?.kind === 'text' ? (
                <pre className="results-viewer__plain-text">{preview.value}</pre>
              ) : preview?.kind === 'image' ? (
                <img src={preview.value} alt={selectedArtifact?.name || 'Workflow chart'} className="results-viewer__image" />
              ) : preview?.kind === 'unsupported' ? (
                <p className="results-viewer__message">Preview is not available for this format. Download the file to open it.</p>
              ) : selectedArtifact && error ? (
                <p className="results-viewer__message">Could not preview this file. You can still download it to view.</p>
              ) : artifacts.length > 0 ? (
                <p className="results-viewer__message">Loading file contents…</p>
              ) : results?.length ? (
                <div className="results-step-list">
                  {results.map((item, index) => {
                    const text = resultText(item);
                    return (
                      <article key={item?.task_id || index} className="step-result">
                        <div className="step-result-heading">
                          <h3>
                            Step {item?.task_id || index + 1} · {item?.description || 'Step output'}
                          </h3>
                          <Badge variant={item?.status === 'failed' ? 'destructive' : 'secondary'}>{item?.status === 'partial' ? 'Partially completed' : (item?.status || 'done')}</Badge>
                        </div>
                        <StructuredText text={text} />
                      </article>
                    );
                  })}
                </div>
              ) : runStatus === 'failed' ? (
                <p className="results-viewer__message">The workflow failed before producing results. Review the failed step or try again.</p>
              ) : (
                <p className="results-viewer__message">The workflow is running. Results will appear here as steps finish.</p>
              )}
            </Card>
            {artifacts.length > 0 && results?.length > 0 && (
              <details className="step-results-details">
                <summary>Step outputs ({results.length})</summary>
                <div className="results-step-list">
                  {results.map((item, index) => (
                    <article key={item?.task_id || index} className="step-result">
                      <div className="step-result-heading">
                        <h3>
                          Step {item?.task_id || index + 1} · {item?.description || 'Step output'}
                        </h3>
                        <Badge variant={item?.status === 'failed' ? 'destructive' : 'secondary'}>{item?.status === 'partial' ? 'Partially completed' : (item?.status || 'done')}</Badge>
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
