import { useState } from 'react';
import type { Finding, Severity } from '../lib/types';

const CONFIDENCE_NOTE: Record<Finding['confidence'], string> = {
  high: 'Reproduced directly. Treat as real.',
  medium: 'Observed once. Worth a quick check before you act on it.',
  low: 'Weak signal. Likely noise, shown so you can judge for yourself.',
};

/*
 * Tailwind scans source for complete class names, so a template string like
 * `bg-${severity}` would never be generated. These lookups keep every class
 * literal and greppable.
 */
const SEVERITY_BAR: Record<Severity, string> = {
  critical: 'bg-critical',
  high: 'bg-high',
  medium: 'bg-medium',
  low: 'bg-low',
};

const SEVERITY_BADGE: Record<Severity, string> = {
  critical: 'text-critical border-critical/40',
  high: 'text-high border-high/40',
  medium: 'text-medium border-medium/40',
  low: 'text-low border-low/40',
};

const BADGE_BASE =
  'rounded-full border px-2 py-0.5 text-[11px] font-medium capitalize tracking-[0.01em]';

export function FindingCard({ finding }: { finding: Finding }) {
  const [open, setOpen] = useState(false);

  return (
    <article className="overflow-hidden rounded-[11px] border border-line bg-raised transition-colors hover:border-line-strong">
      <button
        type="button"
        className="flex w-full items-start gap-3 p-4 text-left"
        aria-expanded={open}
        onClick={() => setOpen((prev) => !prev)}
      >
        <span
          className={`min-h-8.5 w-0.75 flex-none self-stretch rounded-[2px] ${SEVERITY_BAR[finding.severity]}`}
          aria-hidden="true"
        />

        <span className="min-w-0 flex-1">
          <span className="mb-1.5 flex flex-wrap gap-1.5">
            <span
              className={`${BADGE_BASE} ${
                finding.category === 'bug'
                  ? 'border-critical/40 text-critical'
                  : 'border-improve/40 text-improve'
              }`}
            >
              {finding.category === 'bug' ? 'Broken' : 'Improvement'}
            </span>

            <span className={`${BADGE_BASE} ${SEVERITY_BADGE[finding.severity]}`}>
              {finding.severity}
            </span>

            <span
              className={`${BADGE_BASE} cursor-help border-line-strong text-faint`}
              title={CONFIDENCE_NOTE[finding.confidence]}
            >
              {finding.confidence} confidence
            </span>
          </span>

          <span className="block text-[14.5px] leading-[1.4] font-semibold tracking-tight">
            {finding.title}
          </span>

          <span className="mt-1 flex flex-wrap items-center gap-2 text-[12.5px] text-faint">
            {finding.location}
            {finding.selector ? (
              <code className="rounded-[5px] border border-line bg-bg px-1.5 py-px font-mono text-[0.86em]">
                {finding.selector}
              </code>
            ) : null}
          </span>
        </span>

        <svg
          className={`mt-0.5 size-4 flex-none text-faint transition-transform ${open ? 'rotate-180' : ''}`}
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={2}
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <path d="M6 9l6 6 6-6" />
        </svg>
      </button>

      {open ? (
        <div className="grid animate-rise gap-4 border-t border-line px-4.5 pt-4 pb-4.5 pl-7.75">
          <section>
            <h4 className="mb-1.25 text-[11.5px] font-semibold tracking-[0.06em] text-faint uppercase">
              What happened
            </h4>
            <p className="text-sm text-muted">{finding.evidence}</p>
          </section>

          <section>
            <h4 className="mb-1.25 text-[11.5px] font-semibold tracking-[0.06em] text-faint uppercase">
              How to see it yourself
            </h4>
            <ol className="grid list-decimal gap-1 pl-4.5 text-sm text-muted">
              {finding.steps.map((step) => (
                <li key={step}>{step}</li>
              ))}
            </ol>
          </section>

          <section>
            <h4 className="mb-1.25 text-[11.5px] font-semibold tracking-[0.06em] text-faint uppercase">
              Suggested fix
            </h4>
            <p className="text-sm text-muted">{finding.suggestion}</p>
          </section>

          <p className="rounded-lg border border-line bg-bg px-3 py-2.25 text-[12.5px] text-faint">
            {CONFIDENCE_NOTE[finding.confidence]}
          </p>
        </div>
      ) : null}
    </article>
  );
}
