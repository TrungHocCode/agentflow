import { Dialog as DialogPrimitive } from "@base-ui/react/dialog";
import { cn } from "cn";

function Dialog(props) {
  return <DialogPrimitive.Root {...props} />;
}

function DialogClose(props) {
  return <DialogPrimitive.Close {...props} />;
}

function DialogContent({ className, children, ...props }) {
  return (
    <DialogPrimitive.Portal>
      <DialogPrimitive.Backdrop className="system-dialog__backdrop" />
      <DialogPrimitive.Viewport className="system-dialog__viewport">
        <DialogPrimitive.Popup
          className={cn("system-dialog__popup", className)}
          {...props}
        >
          {children}
        </DialogPrimitive.Popup>
      </DialogPrimitive.Viewport>
    </DialogPrimitive.Portal>
  );
}

function DialogHeader({ className, ...props }) {
  return <div className={cn("system-dialog__header", className)} {...props} />;
}

function DialogTitle({ className, ...props }) {
  return <DialogPrimitive.Title className={cn("system-dialog__title", className)} {...props} />;
}

function DialogDescription({ className, ...props }) {
  return <DialogPrimitive.Description className={cn("system-dialog__description", className)} {...props} />;
}

function DialogFooter({ className, ...props }) {
  return <div className={cn("system-dialog__footer", className)} {...props} />;
}

export {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
};
