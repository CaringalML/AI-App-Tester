import { useState } from 'react';
import type { ScanOptions } from '../lib/types';
import { checkUrl } from '../lib/url';

interface Props {
  options: ScanOptions;
  onOptionsChange: (next: ScanOptions) => void;
  onSubmit: (url: string) => void;
  busy: boolean;
}

const TOGGLES: Array<{ key: keyof ScanOptions; label: string; hint: string }> = [
  { key: 'findBugs', label: 'Broken things', hint: 'Errors, dead ends and flows that fail' },
  {
    key: 'findImprovements',
    label: 'Could be better',
    hint: 'Rough edges that still technically work',
  },
  { key: 'checkAccessibility', label: 'Accessibility', hint: 'Contrast, labels and keyboard access' },
];

export function ScanForm({ options, onOptionsChange, onSubmit, busy }: Props) {
  const [value, setValue] = useState('');
  const [error, setError] = useState<string | null>(null);

  function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    const check = checkUrl(value);
    if (!check.ok) {
      setError(check.message);
      return;
    }
    setError(null);
    setValue(check.normalized);
    onSubmit(check.normalized);
  }

  function toggle(key: keyof ScanOptions) {
    onOptionsChange({ ...options, [key]: !options[key] });
  }

  return (
    <form className="mb-7" onSubmit={handleSubmit} noValidate>
      <div
        className={`flex items-center gap-2.5 rounded-[14px] border bg-surface py-1.5 pr-1.5 pl-4 shadow-[var(--shadow-panel)] transition ${
          error
            ? 'border-critical'
            : 'border-line-strong focus-within:border-accent focus-within:ring-4 focus-within:ring-accent/25'
        }`}
      >
        <svg
          className="size-4.25 flex-none text-faint"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={1.8}
          strokeLinecap="round"
          aria-hidden="true"
        >
          <path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7" />
          <path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7" />
        </svg>

        <input
          className="min-w-0 flex-1 border-none bg-transparent py-2.5 text-[15.5px] text-ink placeholder:text-faint focus:outline-none disabled:opacity-55"
          type="text"
          inputMode="url"
          autoComplete="url"
          spellCheck={false}
          placeholder="your-app.com"
          aria-label="Address of the app to test"
          aria-invalid={Boolean(error)}
          value={value}
          disabled={busy}
          onChange={(event) => {
            setValue(event.target.value);
            if (error) setError(null);
          }}
        />

        <button
          type="submit"
          className="group inline-flex flex-none items-center gap-1.5 rounded-[10px] bg-accent px-4 py-2.5 text-[14.5px] font-semibold tracking-tight text-accent-ink transition hover:brightness-110 active:translate-y-px disabled:opacity-75 disabled:hover:brightness-100"
          disabled={busy}
        >
          {busy ? (
            <>
              <span
                className="size-3.25 animate-spin-fast rounded-full border-2 border-current border-r-transparent"
                aria-hidden="true"
              />
              Testing
            </>
          ) : (
            <>
              Run test
              <svg
                className="size-3.75 transition-transform group-hover:translate-x-0.5"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth={2}
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
              >
                <path d="M5 12h13M12 5l7 7-7 7" />
              </svg>
            </>
          )}
        </button>
      </div>

      <p
        className={`mx-0.5 mt-2 text-[13px] ${error ? 'text-critical' : 'text-faint'}`}
        role={error ? 'alert' : undefined}
      >
        {error ?? 'Paste a live address. A staging or preview link works best.'}
      </p>

      <div className="mt-4 flex flex-wrap gap-2" role="group" aria-label="What to look for">
        {TOGGLES.map(({ key, label, hint }) => {
          const on = Boolean(options[key]);
          return (
            <button
              key={key}
              type="button"
              className={`inline-flex items-center gap-1.75 rounded-full border px-3.25 py-1.75 text-[13px] transition disabled:opacity-50 ${
                on
                  ? 'border-accent/30 bg-accent/12 text-ink'
                  : 'border-line bg-surface text-muted hover:border-line-strong hover:text-ink'
              }`}
              aria-pressed={on}
              title={hint}
              disabled={busy}
              onClick={() => toggle(key)}
            >
              <span
                className={`size-1.5 rounded-full transition ${
                  on ? 'bg-accent ring-3 ring-accent/15' : 'bg-faint'
                }`}
                aria-hidden="true"
              />
              {label}
            </button>
          );
        })}
      </div>
    </form>
  );
}
