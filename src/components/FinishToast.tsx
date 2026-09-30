import { useEffect } from 'react';

export interface FinishNotice {
  ok: boolean;
  title: string;
  detail: string;
}

/*
 * Says a watched test has finished, wherever the reader has scrolled to.
 * Announced politely to screen readers; dismisses itself after a while.
 */
export function FinishToast({
  notice,
  onView,
  onClose,
}: {
  notice: FinishNotice | null;
  onView: () => void;
  onClose: () => void;
}) {
  useEffect(() => {
    if (!notice) return;
    const timer = setTimeout(onClose, 9000);
    return () => clearTimeout(timer);
  }, [notice, onClose]);

  return (
    <div
      role="status"
      aria-live="polite"
      className="pointer-events-none fixed inset-x-3 bottom-3 z-50 flex justify-center sm:inset-x-auto sm:right-5 sm:bottom-5"
    >
      {notice ? (
        <div className="pointer-events-auto flex w-full max-w-sm animate-pop items-start gap-3 rounded-[14px] border border-line bg-surface p-3.5 shadow-[0_16px_48px_rgb(0_0_0/0.35)]">
          <span
            className={`grid size-8 flex-none place-items-center rounded-full ${
              notice.ok ? 'bg-low/15 text-low' : 'bg-critical/15 text-critical'
            }`}
          >
            <svg
              className="size-4"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth={2.5}
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <path d={notice.ok ? 'M5 12l4 4L19 6' : 'M12 8v5M12 16.5v.5'} />
            </svg>
          </span>
          <div className="min-w-0 flex-1">
            <p className="text-[13.5px] font-semibold tracking-tight">{notice.title}</p>
            <p className="mt-0.5 text-[12.5px] text-muted">{notice.detail}</p>
            <button
              type="button"
              onClick={onView}
              className="mt-2 rounded-md bg-accent px-2.5 py-1 text-[12px] font-medium text-accent-ink transition hover:brightness-110"
            >
              View results
            </button>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Dismiss"
            className="grid size-6 flex-none place-items-center rounded-md text-faint hover:text-ink"
          >
            <svg
              className="size-3.5"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth={2}
              strokeLinecap="round"
              aria-hidden="true"
            >
              <path d="M6 6l12 12M18 6L6 18" />
            </svg>
          </button>
        </div>
      ) : null}
    </div>
  );
}
