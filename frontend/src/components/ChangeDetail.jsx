import { useEffect, useState } from 'react';
import { ArrowLeft } from 'lucide-react';
import { getChange, getSnapshotContent } from '../services/ciApi';
import { Button } from './ui/button';
import { Card } from './ui/card';
import ErrorNotice from './ErrorNotice';
import LoadingState from './LoadingState';
import { OutcomeBadge } from './OutcomeBadge';

export default function ChangeDetail({ changeId, onBack }) {
  const [change, setChange] = useState(null);
  const [beforeText, setBeforeText] = useState('');
  const [afterText, setAfterText] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [reloadKey, setReloadKey] = useState(0);

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
  }, [changeId, reloadKey]);

  if (loading) return <LoadingState label="Loading change detail" />;
  if (error) {
    return (
      <ErrorNotice
        message={error}
        onRetry={() => setReloadKey((key) => key + 1)}
        onDismiss={() => setError('')}
      />
    );
  }
  if (!change) return null;

  return (
    <div className="flex flex-col gap-3">
      <div className="ci-section-head">
        <div className="ci-title-row">
          <Button type="button" variant="ghost" size="sm" onClick={onBack}>
            <ArrowLeft data-icon="inline-start" />Back
          </Button>
          <OutcomeBadge outcome={change.kind} />
          <span className="ci-row-title">{change.section || 'Preamble'}</span>
        </div>
      </div>
      <div className="grid gap-2 md:grid-cols-2">
        <Card className="ci-section p-3">
          <p className="ci-section-title">Before</p>
          <pre className="ci-diff">{change.before_excerpt || beforeText}</pre>
        </Card>
        <Card className="ci-section p-3">
          <p className="ci-section-title">After</p>
          <pre className="ci-diff">{change.after_excerpt || afterText}</pre>
        </Card>
      </div>
    </div>
  );
}
