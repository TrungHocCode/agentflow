import { AlertCircle, AlertTriangle, CheckCircle2, Info } from 'lucide-react';
import { Alert, AlertDescription } from './ui/alert';
import { Button } from './ui/button';
import { Spinner } from './ui/spinner';
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle
} from './ui/dialog';

const TONE_ICONS = {
  danger: AlertTriangle,
  error: AlertCircle,
  success: CheckCircle2,
  info: Info
};

export default function SystemDialog({ dialog, busy = false, onClose, onConfirm }) {
  if (!dialog) return null;

  const isConfirmation = dialog.kind === 'confirm';
  const Icon = TONE_ICONS[dialog.tone] || Info;

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !busy) onClose();
      }}
    >
      <DialogContent>
        <div className={`system-dialog__icon system-dialog__icon--${dialog.tone || 'info'}`} aria-hidden="true">
          <Icon />
        </div>
        <DialogHeader>
          <DialogTitle>{dialog.title}</DialogTitle>
          <DialogDescription>{dialog.description}</DialogDescription>
        </DialogHeader>
        {dialog.error && (
          <Alert variant="destructive" className="system-dialog__error">
            <AlertCircle />
            <AlertDescription>{dialog.error}</AlertDescription>
          </Alert>
        )}
        <DialogFooter>
          {isConfirmation && (
            <DialogClose render={<Button type="button" variant="outline" disabled={busy} />}>
              Cancel
            </DialogClose>
          )}
          {isConfirmation ? (
            <Button type="button" variant="destructive" onClick={onConfirm} disabled={busy}>
              {busy && <Spinner data-icon="inline-start" />}
              {busy ? 'Deleting…' : dialog.confirmLabel || 'Continue'}
            </Button>
          ) : (
            <DialogClose render={<Button type="button" />}>Got it</DialogClose>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
