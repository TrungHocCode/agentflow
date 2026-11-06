import { useState } from 'react';
import { Download } from 'lucide-react';
import { downloadBrief, saveBlob } from '../services/ciApi';
import { Button } from './ui/button';
import { Card } from './ui/card';
import { Spinner } from './ui/spinner';
import ErrorNotice from './ErrorNotice';
import StructuredText from './StructuredText';
import { OutcomeBadge, QualityBadge, VerificationBadge } from './OutcomeBadge';
import { countLabel, dateLabel, sourceLabel } from '../lib/labels';

export default function BriefView({ brief }) {
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState('');

  async function handleDownload() {
    setDownloading(true);
    setError('');
    try {
      const blob = await downloadBrief(brief.id);
      saveBlob(blob, `brief-${brief.id}.md`);
    } catch (err) {
      setError(err.message);
    } finally {
      setDownloading(false);
    }
  }

  return (
    <div className="flex flex-col gap-3" data-testid="brief-view">
      <div className="ci-section-head">
        <div className="ci-chip-row">
          <OutcomeBadge outcome={brief.outcome} />
          <QualityBadge quality={brief.quality} />
          <span className="tnum text-sm text-muted-foreground">{dateLabel(brief.created_at)}</span>
        </div>
        <Button type="button" variant="outline" onClick={handleDownload} disabled={downloading}>
          {downloading
            ? <Spinner data-icon="inline-start" aria-hidden="true" />
            : <Download data-icon="inline-start" />}
          {downloading ? 'Downloading…' : 'Download Markdown'}
        </Button>
      </div>
      {error && (
        <ErrorNotice
          message={error}
          onRetry={handleDownload}
          onDismiss={() => setError('')}
        />
      )}
      <Card className="ci-section brief-prose p-4">
        <p className="ci-section-title">Summary</p>
        <StructuredText text={brief.summary} />
      </Card>
      <Card className="ci-section p-4">
        <div className="ci-section-head">
          <p className="ci-section-title">Per-source coverage</p>
          <span className="ci-chip ci-chip--quiet">{countLabel((brief.source_coverage || []).length, 'source')}</span>
        </div>
        <ul className="coverage-list">
          {(brief.source_coverage || []).map((entry) => (
            <li key={entry.source_id} className="coverage-row">
              <span className="ci-chip ci-chip--id" title={String(entry.source_id)}>
                {sourceLabel(entry)}
              </span>
              <OutcomeBadge outcome={entry.outcome} />
              <QualityBadge quality={entry.quality} />
              {entry.reason_codes?.length > 0 && (
                <span className="coverage-reason">{entry.reason_codes.join(', ')}</span>
              )}
            </li>
          ))}
        </ul>
      </Card>
      <Card className="ci-section p-4">
        <p className="ci-section-title">Findings</p>
        {(brief.findings || []).length === 0 ? (
          <p className="ci-empty empty-state-fade">No verified findings in this run.</p>
        ) : (
          <ul className="ci-list">
            {brief.findings.map((finding) => (
              <li key={finding.id} className="ci-row-card flex flex-col gap-1.5">
                <span className="ci-chip-row">
                  <VerificationBadge verification={finding.verification} />
                </span>
                <div className="brief-prose"><StructuredText text={finding.text} /></div>
                {finding.rationale && (
                  <div className="ci-row-copy"><StructuredText text={finding.rationale} /></div>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>
      {brief.limitations?.length > 0 && (
        <Card className="ci-section p-4">
          <p className="ci-section-title">Limitations</p>
          <ul className="structured-text list-disc pl-5">
            {brief.limitations.map((limitation, index) => (
              <li key={index}>{limitation}</li>
            ))}
          </ul>
        </Card>
      )}
      {brief.advisory_actions?.length > 0 && (
        <Card className="ci-section p-4">
          <p className="ci-section-title">Advisory actions</p>
          <ul className="structured-text list-disc pl-5">
            {brief.advisory_actions.map((action, index) => (
              <li key={index}>{action}</li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}
