import { useEffect, useState } from 'react';

/**
 * A small icon button that copies `text` and shows a tick for a moment.
 * `text` can be a function so the value is only built when clicked.
 */
export function CopyButton({
  text,
  label,
  className = '',
}: {
  text: string | (() => string);
  /** What is copied, for the tooltip and screen readers, e.g. "the suggested fix". */
  label: string;
  className?: string;
}) {
  const [state, setState] = useState<'idle' | 'done' | 'failed'>('idle');
  useEffect(() => {
    if (state === 'idle') return;
    const timer = setTimeout(() => setState('idle'), 1500);
    return () => clearTimeout(timer);
  }, [state]);

  return (
    <button
      type="button"
      onClick={async (event) => {
        event.stopPropagation();
        try {
          await navigator.clipboard.writeText(typeof text === 'function' ? text() : text);
          setState('done');
        } catch {
          setState('failed');
        }
      }}
      aria-label={state === 'done' ? 'Copied' : `Copy ${label}`}
      title={state === 'done' ? 'Copied' : state === 'failed' ? 'Copy failed' : `Copy ${label}`}
      className={`inline-grid place-items-center rounded-md transition ${
        state === 'done'
          ? 'text-success'
          : state === 'failed'
            ? 'text-critical'
            : 'text-faint hover:bg-raised hover:text-ink'
      } ${className}`}
    >
      <svg
        className="size-3.5"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        {state === 'done' ? (
          <path d="M5 12.5l4.5 4.5L19 7.5" />
        ) : (
          <>
            <rect x="9" y="9" width="11" height="11" rx="2" />
            <path d="M5 15V6a2 2 0 0 1 2-2h8" />
          </>
        )}
      </svg>
    </button>
  );
}
