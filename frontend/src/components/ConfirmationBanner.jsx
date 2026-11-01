import { CheckCircle2, X } from 'lucide-react';
import { Button } from './ui/button';

// Inline success confirmation chosen over a global toaster: no App-level wiring,
// and the message stays next to the action that produced it.
export default function ConfirmationBanner({ message, onDismiss, className = '' }) {
  if (!message) return null;
  return (
    <div role="status" data-testid="confirmation-banner" className={`confirmation-banner ${className}`.trim()}>
      <CheckCircle2 className="confirmation-banner__icon" size={18} aria-hidden="true" />
      <span className="confirmation-banner__message">{message}</span>
      {onDismiss && (
        <Button type="button" variant="ghost" size="icon-xs" onClick={onDismiss} aria-label="Dismiss confirmation">
          <X aria-hidden="true" />
        </Button>
      )}
    </div>
  );
}
