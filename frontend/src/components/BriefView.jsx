import { useState } from 'react';
import { Download } from 'lucide-react';
import { downloadBrief, saveBlob } from '../services/ciApi';
import { Alert, AlertDescription } from './ui/alert';
import { Button } from './ui/button';
import { Card } from './ui/card';
import { OutcomeBadge, QualityBadge, VerificationBadge } from './OutcomeBadge';

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
      <div className="flex flex-wrap items-center gap-2">
        <OutcomeBadge outcome={brief.outcome} />
        <QualityBadge quality={brief.quality} />
        <span className="text-sm text-muted-foreground">{brief.created_at}</span>
        <span className="flex-1" />
        <Button type="button" variant="outline" onClick={handleDownload} disabled={downloading}>
          <Download data-icon="inline-start" /> {downloading ? 'Downloading…' : 'Download Markdown'}
        </Button>
      </div>
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      <Card className="p-4">
        <p className="mb-1 text-sm font-medium">Summary</p>
        <p className="text-sm">{brief.summary}</p>
      </Card>
      <Card className="p-4">
        <p className="mb-2 text-sm font-medium">Per-source coverage</p>
        <ul className="flex flex-col gap-1 text-sm">
          {(brief.source_coverage || []).map((entry) => (
            <li key={entry.source_id} className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-xs">{entry.source_id.slice(0, 8)}</span>
              <OutcomeBadge outcome={entry.outcome} />
              <QualityBadge quality={entry.quality} />
              <span className="text-muted-foreground">{(entry.reason_codes || []).join(', ')}</span>
            </li>
          ))}
        </ul>
      </Card>
      <Card className="p-4">
        <p className="mb-2 text-sm font-medium">Findings</p>
        {(brief.findings || []).length === 0 ? (
          <p className="text-sm text-muted-foreground">No verified findings in this run.</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {brief.findings.map((finding) => (
              <li key={finding.id} className="flex flex-col gap-1 text-sm">
                <span className="flex items-center gap-2">
                  <VerificationBadge verification={finding.verification} />
                </span>
                <span>{finding.text}</span>
                {finding.rationale && <span className="text-muted-foreground">{finding.rationale}</span>}
              </li>
            ))}
          </ul>
        )}
      </Card>
      {brief.limitations?.length > 0 && (
        <Card className="p-4">
          <p className="mb-1 text-sm font-medium">Limitations</p>
          <ul className="list-disc pl-5 text-sm">
            {brief.limitations.map((limitation, index) => (
              <li key={index}>{limitation}</li>
            ))}
          </ul>
        </Card>
      )}
      {brief.advisory_actions?.length > 0 && (
        <Card className="p-4">
          <p className="mb-1 text-sm font-medium">Advisory actions</p>
          <ul className="list-disc pl-5 text-sm">
            {brief.advisory_actions.map((action, index) => (
              <li key={index}>{action}</li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}
