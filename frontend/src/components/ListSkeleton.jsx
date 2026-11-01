import { Skeleton } from './ui/skeleton';

// Shape-matched placeholders keep list layout stable while data loads.
export default function ListSkeleton({ label = 'Loading', rows = 3, compact = false, className = '' }) {
  const count = Math.max(1, rows);
  return (
    <div
      className={`skeleton-list list-skeleton ${className}`.trim()}
      role="status"
      aria-busy="true"
      aria-label={label}
      data-testid="list-skeleton"
    >
      {Array.from({ length: count }, (_, index) => (
        <div key={index} className={`skeleton-card${compact ? ' skeleton-card--compact' : ''}`}>
          <div className="skeleton-card__row">
            <Skeleton className="skeleton-line skeleton-line--title" />
            <Skeleton className="skeleton-pill" />
          </div>
          <Skeleton className="skeleton-line skeleton-line--meta" />
          {!compact && <Skeleton className="skeleton-line skeleton-line--short" />}
        </div>
      ))}
    </div>
  );
}
