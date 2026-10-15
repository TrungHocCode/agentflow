import { useEffect, useState } from 'react';
import { askHistory, getTimeline } from '../services/ciApi';
import { Alert, AlertDescription } from './ui/alert';
import { Button } from './ui/button';
import { Card } from './ui/card';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { Spinner } from './ui/spinner';

export default function TimelineAndAsk({ watchlistId }) {
  const [events, setEvents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [question, setQuestion] = useState('');
  const [answer, setAnswer] = useState(null);
  const [asking, setAsking] = useState(false);

  async function load() {
    setLoading(true);
    setError('');
    try {
      setEvents(await getTimeline(watchlistId));
    } catch (err) {
      setError(err.message);
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
    try {
      setAnswer(await askHistory(watchlistId, question));
    } catch (err) {
      setError(err.message);
    } finally {
      setAsking(false);
    }
  }

  if (loading) return <Spinner aria-label="Loading timeline" />;

  return (
    <div className="flex flex-col gap-3">
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      <form onSubmit={handleAsk} className="flex gap-2">
        <div className="flex flex-1 flex-col gap-1">
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
            {asking ? 'Asking…' : 'Ask'}
          </Button>
        </div>
      </form>
      {answer && (
        <Card className="p-3" data-testid="history-answer">
          <p className="mb-1 text-sm font-medium">
            {answer.verdict === 'answered' ? `Answered (${answer.intent})` : 'Not observed'}
          </p>
          <p className="whitespace-pre-wrap text-sm">{answer.text}</p>
          {answer.citations?.length > 0 && (
            <ul className="mt-2 flex flex-col gap-1">
              {answer.citations.map((citation, index) => (
                <li key={index} className="text-xs text-muted-foreground">
                  Evidence {index + 1}: snapshot {String(citation.snapshot_id).slice(0, 8)}…
                  offsets {citation.start_offset}–{citation.end_offset}
                </li>
              ))}
            </ul>
          )}
        </Card>
      )}
      {events.length === 0 ? (
        <p className="text-sm text-muted-foreground">No recorded events yet. Completed runs appear here.</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {events.map((event) => (
            <li key={event.id}>
              <Card className="p-3">
                <p className="text-sm">
                  <span className="font-medium">{event.kind.replaceAll('_', ' ')}</span>
                  {' · '}
                  <span className="text-muted-foreground">{event.at}</span>
                </p>
                <p className="text-sm">{event.summary}</p>
              </Card>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
