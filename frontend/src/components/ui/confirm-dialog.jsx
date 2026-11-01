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
      className="fixed inset-0 z-50 flex items-center justify-center bg-[rgba(20,26,25,0.45)] p-4 backdrop-blur-[3px]"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onCancel?.();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="confirm-dialog-title"
        aria-describedby={description ? 'confirm-dialog-description' : undefined}
        className="w-full max-w-md rounded-2xl border border-border bg-card p-5 text-card-foreground shadow-[0_24px_70px_rgba(20,26,25,0.22)] [animation:dialogIn_200ms_ease-out_both]"
      >
        <h2 id="confirm-dialog-title" className="text-lg font-semibold tracking-[-0.02em]">{title}</h2>
        {description && (
          <p id="confirm-dialog-description" className="mt-1.5 text-sm leading-relaxed text-muted-foreground">{description}</p>
        )}
        <div className="mt-5 flex justify-end gap-2">
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
