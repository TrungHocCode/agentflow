import { Spinner } from './ui/spinner';

// Detail screens keep a centered spinner so the label explains what is loading.
export default function LoadingState({ label = 'Loading' }) {
  return (
    <div className="loading-state" role="status" aria-label={label}>
      <Spinner aria-hidden="true" />
      <span>{label}</span>
    </div>
  );
}
