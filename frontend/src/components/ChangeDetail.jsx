import { useEffect, useState } from 'react';
import { getChange, getSnapshotContent } from '../services/ciApi';
import { Alert, AlertDescription } from './ui/alert';
import { Button } from './ui/button';
import { Card } from './ui/card';
import { Spinner } from './ui/spinner';
import { OutcomeBadge } from './OutcomeBadge';

export default function ChangeDetail({ changeId, onBack }) {
  const [change, setChange] = useState(null);
  const [beforeText, setBeforeText] = useState('');
  const [afterText, setAfterText] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      setError('');
      try {
        const detail = await getChange(changeId);
        if (cancelled) return;
        setChange(detail);
        const [before, after] = await Promise.all([
          getSnapshotContent(detail.before_snapshot_id),
          getSnapshotContent(detail.after_snapshot_id)
        ]);
        if (!cancelled) {
          setBeforeText(before.text || '');
          setAfterText(after.text || '');
        }
      } catch (err) {
        if (!cancelled) setError(err.message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [changeId]);

  if (loading) return <Spinner aria-label="Loading change detail" />;
  if (error) {
    return (
      <Alert variant="destructive">
        <AlertDescription>{error}</AlertDescription>
      </Alert>
    );
  }
  if (!change) return null;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <Button type="button" variant="ghost" onClick={onBack}>
          Back
        </Button>
        <OutcomeBadge outcome={change.kind} />
        <span className="font-medium">{change.section || 'Preamble'}</span>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <Card className="p-3">
          <p className="mb-1 text-sm font-medium">Before</p>
          <pre className="whitespace-pre-wrap text-sm">{change.before_excerpt || beforeText}</pre>
        </Card>
        <Card className="p-3">
          <p className="mb-1 text-sm font-medium">After</p>
          <pre className="whitespace-pre-wrap text-sm">{change.after_excerpt || afterText}</pre>
        </Card>
      </div>
    </div>
  );
}
