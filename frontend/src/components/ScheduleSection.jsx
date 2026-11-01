import { useEffect, useState } from 'react';
import { createSchedule, listSchedules, updateSchedule } from '../services/ciApi';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { Spinner } from './ui/spinner';
import ConfirmationBanner from './ConfirmationBanner';
import ErrorNotice from './ErrorNotice';
import LoadingState from './LoadingState';

const TIMEZONES = ['UTC', 'Asia/Bangkok', 'Asia/Singapore', 'Asia/Tokyo', 'Europe/London', 'America/New_York'];
const WEEKDAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];

export default function ScheduleSection({ watchlistId }) {
  const [schedules, setSchedules] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [cadence, setCadence] = useState('weekly');
  const [timezone, setTimezone] = useState('UTC');
  const [runAt, setRunAt] = useState('08:00');
  const [dayOfWeek, setDayOfWeek] = useState(1);
  const [saving, setSaving] = useState(false);
  const [togglingId, setTogglingId] = useState('');
  const [notice, setNotice] = useState('');

  async function load() {
    setLoading(true);
    setError('');
    try {
      setSchedules(await listSchedules(watchlistId));
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

  async function handleCreate(event) {
    event.preventDefault();
    setSaving(true);
    setError('');
    try {
      await createSchedule(watchlistId, {
        cadence,
        timezone,
        run_at: runAt,
        day_of_week: cadence === 'weekly' ? Number(dayOfWeek) : null
      });
      await load();
      setNotice('Schedule added.');
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  async function handleToggle(schedule) {
    setTogglingId(schedule.id);
    setError('');
    try {
      await updateSchedule(watchlistId, schedule.id, { active: !schedule.active });
      await load();
      setNotice('Schedule updated.');
    } catch (err) {
      setError(err.message);
    } finally {
      setTogglingId('');
    }
  }

  if (loading) return <LoadingState label="Loading schedules" />;

  return (
    <div className="flex flex-col gap-3">
      {error && (
        <ErrorNotice message={error} onRetry={load} onDismiss={() => setError('')} />
      )}
      {notice && (
        <ConfirmationBanner message={notice} onDismiss={() => setNotice('')} />
      )}
      {schedules.length > 0 && (
        <ul className="schedule-list">
          {schedules.map((schedule) => (
            <li key={schedule.id} className="ci-row-card flex flex-wrap items-center gap-2">
              <span className="text-sm">
                {schedule.cadence} {schedule.day_of_week !== null && schedule.cadence === 'weekly'
                  ? `on ${WEEKDAYS[schedule.day_of_week]} ` : ''}at {schedule.run_at} ({schedule.timezone})
              </span>
              <span className={`ci-chip ${schedule.active ? 'ci-pill--calm' : 'ci-chip--quiet'}`}>
                {schedule.active ? `next due ${schedule.next_due_at}` : 'disabled'}
              </span>
              <span className="flex-1" />
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => handleToggle(schedule)}
                disabled={togglingId === schedule.id}
              >
                {togglingId === schedule.id && <Spinner data-icon="inline-start" aria-hidden="true" />}
                {schedule.active ? 'Disable' : 'Enable'}
              </Button>
            </li>
          ))}
        </ul>
      )}
      <form onSubmit={handleCreate} className="schedule-form">
        <div className="flex flex-col gap-1">
          <Label htmlFor="ci-sched-cadence">Cadence</Label>
          <select id="ci-sched-cadence" value={cadence} onChange={(event) => setCadence(event.target.value)} className="ci-select">
            <option value="weekly">weekly</option>
            <option value="daily">daily</option>
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor="ci-sched-tz">Timezone</Label>
          <select id="ci-sched-tz" value={timezone} onChange={(event) => setTimezone(event.target.value)} className="ci-select">
            {TIMEZONES.map((zone) => (
              <option key={zone} value={zone}>{zone}</option>
            ))}
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor="ci-sched-time">Run at (HH:MM)</Label>
          <Input id="ci-sched-time" value={runAt} onChange={(event) => setRunAt(event.target.value)} placeholder="08:00" required pattern="[0-9]{2}:[0-9]{2}" />
        </div>
        {cadence === 'weekly' && (
          <div className="flex flex-col gap-1">
            <Label htmlFor="ci-sched-dow">Day of week</Label>
            <select id="ci-sched-dow" value={dayOfWeek} onChange={(event) => setDayOfWeek(event.target.value)} className="ci-select">
              {WEEKDAYS.map((day, index) => (
                <option key={day} value={index}>{day}</option>
              ))}
            </select>
          </div>
        )}
        <div className="col-span-2">
          <Button type="submit" disabled={saving}>
            {saving && <Spinner data-icon="inline-start" aria-hidden="true" />}
            {saving ? 'Saving…' : 'Add schedule'}
          </Button>
        </div>
      </form>
    </div>
  );
}
