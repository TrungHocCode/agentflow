import { AlertTriangle, RotateCcw, X } from 'lucide-react';
import { Button } from './ui/button';

const REFERENCE_PATTERN = /\s*\((?:reference(?: ID)?|ref):\s*([^)]+)\)\s*$/i;

function splitReference(message) {
  const text = typeof message === 'string' ? message : String(message?.message || message || '');
  const match = text.match(REFERENCE_PATTERN);
  if (!match) return { text: text.trim(), reference: '' };
  return { text: text.replace(REFERENCE_PATTERN, '').trim(), reference: match[1].trim() };
}

export default function ErrorNotice({
  title = '',
  message,
  reference = '',
  onRetry,
  onDismiss,
  retryLabel = 'Retry',
  className = ''
}) {
  if (!message) return null;
  const parsed = splitReference(message);
  const referenceValue = reference || parsed.reference;

  return (
    <div role="alert" data-testid="error-notice" className={`error-notice ${className}`.trim()}>
      <AlertTriangle className="error-notice__icon" size={18} aria-hidden="true" />
      <div className="error-notice__body">
        {title && <p className="error-notice__title">{title}</p>}
        <p className="error-notice__message">{parsed.text}</p>
        {referenceValue && (
          <p className="error-notice__reference" data-testid="error-reference">
            Reference: {referenceValue}
          </p>
        )}
        {(onRetry || onDismiss) && (
          <div className="error-notice__actions">
            {onRetry && (
              <Button type="button" variant="outline" size="xs" onClick={onRetry}>
                <RotateCcw data-icon="inline-start" aria-hidden="true" />
                {retryLabel}
              </Button>
            )}
            {onDismiss && (
              <Button
                type="button"
                variant="ghost"
                size="icon-xs"
                onClick={onDismiss}
                aria-label="Dismiss error"
              >
                <X aria-hidden="true" />
              </Button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
