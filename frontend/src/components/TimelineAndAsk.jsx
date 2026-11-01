import { useEffect, useState } from 'react';
import { askHistory, getTimeline } from '../services/ciApi';
import { Button } from './ui/button';
import { Card } from './ui/card';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { Spinner } from './ui/spinner';
import ErrorNotice from './ErrorNotice';
import ListSkeleton from './ListSkeleton';

export default function TimelineAndAsk({ watchlistId }) {
  const [events, setEvents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [errorSource, setErrorSource] = useState('');
  const [question, setQuestion] = useState('');
  const [answer, setAnswer] = useState(null);
  const [asking, setAsking] = useState(false);

  async function load() {
    setLoading(true);
    setError('');
    setErrorSource('');
    try {
      setEvents(await getTimeline(watchlistId));
    } catch (err) {
      setError(err.message);
      setErrorSource('load');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [watchlistId]);

  async function handleAsk(event) {
    event.preventDefault();
    setAsking(true);
    setError('');
    setErrorSource('');
    try {
      setAnswer(await askHistory(watchlistId, question));
    } catch (err) {
      setError(err.message);
      setErrorSource('ask');
    } finally {
      setAsking(false);
    }
  }

  if (loading) return <ListSkeleton label="Loading timeline" rows={3} />;

  return (
    <div className="flex flex-col gap-3">
      {error && (
        <ErrorNotice
          message={error}
          onRetry={errorSource === 'load' ? load : undefined}
          onDismiss={() => setError('')}
        />
      )}
      <form onSubmit={handleAsk} className="timeline-form">
        <div className="flex flex-col gap-1 timeline-form__field">
          <Label htmlFor="ci-ask-question">Ask history</Label>
          <Input
            id="ci-ask-question"
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            placeholder="When did the Pricing section change?"
            required
          />
        </div>
        <div className="flex items-end">
          <Button type="submit" disabled={asking}>
            {asking && <Spinner data-icon="inline-start" aria-hidden="true" />}
            {asking ? 'Asking…' : 'Ask'}
          </Button>
        </div>
      </form>
      {answer && (
        <Card className="ci-section p-3" data-testid="history-answer">
          <p className="ci-section-title">
            {answer.verdict === 'answered' ? `Answered (${answer.intent})` : 'Not observed'}
          </p>
          <p className="ci-row-copy whitespace-pre-wrap">{answer.text}</p>
          {answer.citations?.length > 0 && (
            <ul className="citation-list">
              {answer.citations.map((citation, index) => (
                <li key={index} className="citation-chip" title={String(citation.snapshot_id)}>
                  <span className="ci-chip ci-chip--id">Evidence {index + 1}</span>
                  <span className="type-mono">
                    snapshot {String(citation.snapshot_id).slice(0, 8)}… offsets {citation.start_offset}–{citation.end_offset}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      )}
      {events.length === 0 ? (
        <p className="ci-empty empty-state-fade">No recorded events yet. Completed runs appear here.</p>
      ) : (
        <ul className="timeline-list">
          {events.map((event) => (
            <li key={event.id} className="ci-row-card">
              <p className="flex flex-wrap items-baseline gap-2">
                <span className="timeline-item__kind">{event.kind.replaceAll('_', ' ')}</span>
                <span className="tnum text-xs text-muted-foreground">{event.at}</span>
              </p>
              <p className="ci-row-copy mt-1">{event.summary}</p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
