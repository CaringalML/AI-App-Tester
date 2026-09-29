import { useEffect, useRef, type ReactNode } from 'react';
import { createPortal } from 'react-dom';

/*
 * A modal confirmation for actions that cannot be undone.
 *
 * Rendered into document.body through a portal: inside the sidebar it would be
 * clipped by the rail's overflow, and inside the phone drawer, whose animation
 * uses a transform, `position: fixed` would stop being relative to the screen.
 *
 * Behaves like a native dialog: focus starts on Cancel (the safe choice), Tab
 * stays inside, Escape and the backdrop cancel, the page behind does not
 * scroll, and focus returns to whatever opened it.
 */

interface Props {
  open: boolean;
  title: string;
  children?: ReactNode;
  confirmLabel: string;
  busy?: boolean;
  error?: string | null;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmDialog({
  open,
  title,
  children,
  confirmLabel,
  busy = false,
  error,
  onConfirm,
  onCancel,
}: Props) {
  const panelRef = useRef<HTMLDivElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  // Read through a ref so the key handler never closes over a stale `busy`.
  const busyRef = useRef(busy);
  busyRef.current = busy;

  useEffect(() => {
    if (!open) return;
    const opener = document.activeElement as HTMLElement | null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    cancelRef.current?.focus();

    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        event.preventDefault();
        if (!busyRef.current) onCancel();
        return;
      }
      if (event.key !== 'Tab' || !panelRef.current) return;
      const focusable = [
        ...panelRef.current.querySelectorAll<HTMLElement>('button:not([disabled])'),
      ];
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = previousOverflow;
      opener?.focus?.();
    };
    // Runs once per opening. The handler keeps the onCancel from that render, which is
    // safe: cancelling only closes the dialog, and `busy` is read through a ref.
  }, [open]);

  if (!open) return null;

  return createPortal(
    <div className="fixed inset-0 z-50 grid place-items-center p-4">
      <button
        type="button"
        aria-label="Cancel"
        tabIndex={-1}
        className="absolute inset-0 animate-fade bg-black/60 backdrop-blur-[2px]"
        onClick={() => !busy && onCancel()}
      />
      <div
        ref={panelRef}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="confirm-title"
        aria-describedby="confirm-body"
        className="relative w-full max-w-sm animate-pop rounded-[14px] border border-line bg-surface p-5 shadow-[0_24px_64px_rgb(0_0_0/0.45)]"
      >
        <div className="flex gap-3.5">
          <span className="grid size-10 flex-none place-items-center rounded-full bg-critical/12 text-critical">
            <svg
              className="size-5"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth={2}
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <path d="M4 7h16M10 11v6M14 11v6M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4h6v3" />
            </svg>
          </span>
          <div className="min-w-0 flex-1">
            <h2 id="confirm-title" className="text-[15px] font-semibold tracking-tight">
              {title}
            </h2>
            <div id="confirm-body" className="mt-1.5 text-[13px] leading-relaxed text-muted">
              {children}
            </div>
          </div>
        </div>

        {error ? (
          <p
            role="alert"
            className="mt-4 rounded-lg border border-critical/40 bg-critical/5 px-3 py-2 text-[12.5px] text-critical"
          >
            {error}
          </p>
        ) : null}

        <div className="mt-5 flex justify-end gap-2">
          <button
            ref={cancelRef}
            type="button"
            onClick={onCancel}
            disabled={busy}
            className="rounded-lg border border-line-strong px-3.5 py-2 text-[13px] text-muted transition hover:text-ink disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={busy}
            className="flex items-center gap-2 rounded-lg bg-critical px-3.5 py-2 text-[13px] font-medium text-white transition hover:brightness-110 disabled:opacity-80"
          >
            {busy ? (
              <span className="size-3.5 animate-spin-fast rounded-full border-2 border-current border-r-transparent" />
            ) : null}
            {busy ? 'Deleting…' : confirmLabel}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
