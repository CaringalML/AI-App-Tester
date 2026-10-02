import { useState, type ReactNode } from 'react';
import { bugText, readableEvidence, recordedEvidence, stepsText } from '../lib/copyText';
import type { Finding, Severity } from '../lib/types';
import { CopyButton } from './CopyButton';
import { InspectSection } from './InspectSection';
import { LinkedText } from './LinkedText';
import { RegressionTest } from './RegressionTest';

const CONFIDENCE_NOTE: Record<Finding['confidence'], string> = {
  high: 'Reproduced directly. Treat as real.',
  medium: 'Observed once. Worth a quick check before you act on it.',
  low: 'Weak signal. Likely noise, shown so you can judge for yourself.',
};

const SOURCE_NOTE: Record<NonNullable<Finding['source']>, string> = {
  automated: 'Recorded directly by the browser: an error, failed request or rule violation.',
  agent: 'Reported by Claude while exploring, backed by the browser evidence it cites.',
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

/** "#a48d78 text on #e6dac8" or "#ffffff on #887564": a measured text/background pair. */
const COLOUR_PAIR = /(#[0-9a-f]{6}) (?:text )?on (#[0-9a-f]{6})/i;

/** A sample of text in exact colours, so contrast can be seen, not just read about. */
function Swatch({ fg, bg }: { fg: string; bg: string }) {
  return (
    <span
      aria-hidden="true"
      className="flex-none rounded px-1.5 font-sans text-[12px] font-semibold"
      style={{ color: fg, background: bg }}
    >
      Aa
    </span>
  );
}

/** One line of the browser log; a colour contrast line leads with a sample. */
function LogLine({ line }: { line: string }) {
  const pair = COLOUR_PAIR.exec(line);
  return (
    <li className="flex items-baseline gap-2">
      {pair ? <Swatch fg={pair[1]} bg={pair[2]} /> : null}
      <span className="min-w-0">{line}</span>
    </li>
  );
}

/** "text #a48d78 → #705d4b on #e6dac8" and "background #887564 → #857262 behind #ffffff text". */
const FIX_TEXT = /text (#[0-9a-f]{6}) → (#[0-9a-f]{6}) on (#[0-9a-f]{6})/i;
const FIX_BACKGROUND = /background (#[0-9a-f]{6}) → (#[0-9a-f]{6}) behind (#[0-9a-f]{6}) text/i;

/** A suggested colour change, shown as it looks now and after the change. */
function FixLine({ line }: { line: string }) {
  const text = FIX_TEXT.exec(line);
  const background = FIX_BACKGROUND.exec(line);
  const before = text
    ? { fg: text[1], bg: text[3] }
    : background && { fg: background[3], bg: background[1] };
  const after = text
    ? { fg: text[2], bg: text[3] }
    : background && { fg: background[3], bg: background[2] };
  return (
    <li className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
      {before && after ? (
        <span className="flex flex-none items-center gap-1" title="Now, and after the change">
          <Swatch {...before} />
          <span aria-hidden="true" className="text-faint">
            →
          </span>
          <Swatch {...after} />
        </span>
      ) : null}
      <span className="min-w-0 font-mono text-[12.5px]">{line}</span>
    </li>
  );
}

/** The fix as prose, with any "- " lines shown as a list of changes. */
function SuggestedFix({ text }: { text: string }) {
  const lines = text.split('\n');
  const items = lines.filter((line) => line.startsWith('- ')).map((line) => line.slice(2));
  const [intro, ...rest] = lines.filter((line) => !line.startsWith('- '));
  return (
    <div className="grid gap-2 text-sm text-muted">
      {intro ? (
        <p>
          <LinkedText text={intro} />
        </p>
      ) : null}
      {items.length ? (
        <ul className="grid gap-1.5">
          {items.map((item, index) => (
            <FixLine key={index} line={item} />
          ))}
        </ul>
      ) : null}
      {rest.map((line, index) => (
        <p key={index}>
          <LinkedText text={line} />
        </p>
      ))}
    </div>
  );
}

/** A section heading with a copy button for just that section. */
function SectionHeading({
  children,
  copy,
  label,
}: {
  children: ReactNode;
  copy?: () => string;
  label?: string;
}) {
  return (
    <h4 className="mb-1.25 flex items-center gap-1.5 text-[11.5px] font-semibold tracking-[0.06em] text-faint uppercase">
      {children}
      {copy && label ? <CopyButton text={copy} label={label} className="size-5.5" /> : null}
    </h4>
  );
}

const BADGE_BASE =
  'rounded-full border px-2 py-0.5 text-[11px] font-medium capitalize tracking-[0.01em]';

export function FindingCard({
  finding,
  step,
  targetUrl,
  onShowInReplay,
}: {
  finding: Finding;
  /** The tested site, so a copied bug names the exact page. */
  targetUrl?: string;
  /** The command log step that shows this finding, numbered as in the log. */
  step?: number;
  /** Present when the replay has a step that shows this finding happening. */
  onShowInReplay?: () => void;
}) {
  const [open, setOpen] = useState(false);
  // Findings that explain themselves in prose carry their browser log after a marker.
  const log = recordedEvidence(finding.evidence);
  return (
    <article className="relative min-w-0 overflow-hidden rounded-[11px] border border-line bg-raised transition-colors hover:border-line-strong">
      {/* Outside the header button, since a button cannot hold another button. */}
      <div className="absolute top-3.5 right-11 flex items-center gap-1.5">
        <CopyButton
          text={() => bugText(finding, targetUrl)}
          label={finding.category === 'bug' ? 'the bug' : 'this finding'}
          className="size-6.5 rounded-full! border border-line-strong bg-bg"
        />
        {step !== undefined && onShowInReplay ? (
          <button
            type="button"
            onClick={onShowInReplay}
            title={`Show step ${step} of the command log in the replay`}
            className="flex items-center gap-1 rounded-full border border-line-strong bg-bg px-2 py-0.5 font-mono text-[11px] text-muted transition hover:border-accent/50 hover:text-accent"
          >
            <svg
              className="size-3"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth={2}
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <path d="M9 6h11M9 12h11M9 18h11M4 6h.01M4 12h.01M4 18h.01" />
            </svg>
            Step {step}
          </button>
        ) : null}
      </div>
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
          <span
            className={`mb-1.5 flex flex-wrap gap-1.5 ${step !== undefined ? 'pr-28' : 'pr-8'}`}
          >
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

            {finding.source ? (
              <span
                className={`${BADGE_BASE} cursor-help normal-case ${
                  finding.source === 'automated'
                    ? 'border-low/40 text-low'
                    : 'border-accent/40 text-accent'
                }`}
                title={SOURCE_NOTE[finding.source]}
              >
                {finding.source === 'automated' ? 'Browser verified' : 'Found by Claude'}
              </span>
            ) : null}
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
        <div className="grid animate-rise grid-cols-[minmax(0,1fr)] gap-4 [overflow-wrap:anywhere] border-t border-line px-4.5 pt-4 pb-4.5 pl-7.75">
          <section>
            <SectionHeading copy={() => bugText(finding, targetUrl)} label="what happened">
              What happened
            </SectionHeading>
            {finding.source === 'agent' || log.length ? (
              <>
                <p className="text-sm leading-relaxed whitespace-pre-wrap text-muted">
                  {readableEvidence(finding.evidence)}
                </p>
                {log.length ? (
                  <div className="mt-2.5 rounded-lg border border-line bg-bg px-3 py-2">
                    <p className="mb-1 text-[11px] font-semibold tracking-[0.06em] text-faint uppercase">
                      What the browser recorded
                    </p>
                    <ul className="grid gap-1 font-mono text-[12px] leading-relaxed text-muted">
                      {log.map((line, index) => (
                        <LogLine key={index} line={line} />
                      ))}
                    </ul>
                  </div>
                ) : null}
              </>
            ) : (
              // Browser checks report raw data (HTML snippets, URLs), which reads best as-is.
              <pre className="font-mono text-[12.5px] leading-relaxed [overflow-wrap:anywhere] whitespace-pre-wrap text-muted">
                {finding.evidence}
              </pre>
            )}
          </section>

          {finding.inspect?.length ? <InspectSection targets={finding.inspect} /> : null}

          <RegressionTest finding={finding} />

          {finding.screenshotUrl ? (
            <section>
              <SectionHeading>Screen at the time</SectionHeading>
              <a href={finding.screenshotUrl} target="_blank" rel="noreferrer">
                <img
                  src={finding.screenshotUrl}
                  alt={`Browser viewport when “${finding.title}” was recorded`}
                  loading="lazy"
                  className="max-h-64 rounded-lg border border-line"
                />
              </a>
            </section>
          ) : null}

          <section>
            <SectionHeading copy={() => stepsText(finding)} label="the steps">
              How to see it yourself
            </SectionHeading>
            <ol className="grid list-decimal gap-1 pl-4.5 text-sm text-muted">
              {finding.steps.map((step, index) => (
                <li key={index}>
                  <LinkedText text={step} />
                </li>
              ))}
            </ol>
          </section>

          <section>
            <SectionHeading copy={() => finding.suggestion} label="the suggested fix">
              Suggested fix
            </SectionHeading>
            <SuggestedFix text={finding.suggestion} />
          </section>

          <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-line bg-bg px-3 py-2.25 text-[12.5px] text-faint">
            <span>{CONFIDENCE_NOTE[finding.confidence]}</span>
            {onShowInReplay ? (
              <button
                type="button"
                onClick={onShowInReplay}
                className="rounded-md border border-accent/40 px-2.5 py-1 text-[12px] text-accent transition hover:bg-accent/10"
              >
                {step !== undefined ? `Show step ${step} in replay` : 'Show in replay'}
              </button>
            ) : null}
          </div>
        </div>
      ) : null}
    </article>
  );
}
