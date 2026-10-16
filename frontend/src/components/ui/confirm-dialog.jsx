import { useEffect } from 'react';
import { cn } from 'cn';
import { Button } from './button';

// Lightweight accessible confirmation dialog; replaces window.confirm for
// consequential actions so behavior is consistent across browsers/webviews.
function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  tone = 'default',
  onConfirm,
  onCancel
}) {
  useEffect(() => {
    if (!open) return undefined;
    const onKeyDown = (event) => {
      if (event.key === 'Escape') onCancel?.();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [open, onCancel]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onCancel?.();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="confirm-dialog-title"
        aria-describedby={description ? 'confirm-dialog-description' : undefined}
        className="w-full max-w-sm rounded-lg border border-border bg-card p-4 text-card-foreground shadow-lg"
      >
        <h2 id="confirm-dialog-title" className="text-base font-semibold">{title}</h2>
        {description && (
          <p id="confirm-dialog-description" className="mt-1 text-sm text-muted-foreground">{description}</p>
        )}
        <div className="mt-4 flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onCancel}>{cancelLabel}</Button>
          <Button
            type="button"
            autoFocus
            variant={tone === 'destructive' ? 'destructive' : 'default'}
            className={cn(tone === 'destructive' && 'bg-destructive/10')}
            onClick={onConfirm}
          >
            {confirmLabel}
          </Button>
        </div>
      </div>
    </div>
  );
}

export { ConfirmDialog };
