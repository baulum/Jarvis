import { Button, Dialog } from "./ui";
import type { ReactNode } from "react";

/** Yes/no question before a disruptive action (pause, cancel …). Cancel is focused by default. */
export function ConfirmDialog({
  open,
  title,
  children,
  confirmLabel,
  tone = "default",
  busy = false,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: string;
  children: ReactNode;
  confirmLabel: string;
  tone?: "default" | "danger";
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <Dialog
      open={open}
      onClose={onCancel}
      title={title}
      tone={tone}
      footer={
        <>
          <Button variant="ghost" onClick={onCancel} data-autofocus>
            Zurück
          </Button>
          <Button variant={tone === "danger" ? "danger" : "primary"} busy={busy} onClick={onConfirm}>
            {confirmLabel}
          </Button>
        </>
      }
    >
      {children}
    </Dialog>
  );
}
