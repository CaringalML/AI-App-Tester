import { useMemo, useRef, useState } from 'react';
import type { Category, Finding, ScanPhase, ScanResult, TimelineStep } from '../lib/types';
import { SEVERITY_ORDER } from '../lib/types';
import { reportUrl } from '../lib/api';
import { FindingCard } from './FindingCard';
import { TestRunner } from './TestRunner';
import { durationBetween, formatDuration, useElapsed } from '../lib/time';

/** A real scan produces 30+ steps; the log shows the most recent ones. */
const VISIBLE_STEPS = 12;

interface Props {
  phase: ScanPhase;
  progress: string[];
  result: ScanResult | null;
  error: string | null;
  /** Live command log while running; the finished scan carries its own. */
  timeline: TimelineStep[];
  targetUrl?: string;
  /** Client clock when the scan started, for the live timer. */
  runStartedAt: number | null;
}

type Filter = 'all' | Category;

const PANEL = 'rounded-[14px] border border-line bg-surface';
/** Idle, error and placeholder states stay form-width; the runner and results use the full width. */
const NARROW = 'mx-auto w-full max-w-193';

export function FindingsPanel({
  phase,
  progress,
  result,
  error,
  timeline,
  targetUrl,
  runStartedAt,
}: Props) {
  const [filter, setFilter] = useState<Filter>('all');
  const [pinned, setPinned] = useState<number | null>(null);
  const runnerRef = useRef<HTMLDivElement>(null);
  const steps = result?.timeline?.length ? result.timeline : timeline;
  const elapsed = useElapsed(runStartedAt, phase === 'running');
  const duration = result ? durationBetween(result.startedAt, result.finishedAt) : null;

  /** The replay step that shows a finding: where it was reported, else its first cited action. */
  function stepFor(finding: Finding): TimelineStep | undefined {
    return (
      steps.find((s) => s.findingId === finding.id) ??
      steps.find((s) => s.actionId && finding.evidenceIds?.includes(s.actionId))
    );
  }

  function showInReplay(step: TimelineStep) {
    setPinned(step.index);
    runnerRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  const sorted = useMemo(() => {
    if (!result) return [];
    return [...result.findings].sort(
      (a, b) => SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity],
    );
  }, [result]);

  const visible = sorted.filter((finding) => filter === 'all' || finding.category === filter);
  const bugCount = sorted.filter((finding) => finding.category === 'bug').length;
  const improvementCount = sorted.length - bugCount;

  if (phase === 'idle') {
    return (
      <div className={`${PANEL} ${NARROW} px-7 py-11 text-center`}>
        <div className="mb-4.5 flex h-8.5 items-end justify-center gap-1.5" aria-hidden="true">
          <span className="h-3.5 w-1.5 animate-bob rounded-[3px] bg-line-strong" />
          <span className="h-6.5 w-1.5 animate-bob rounded-[3px] bg-line-strong [animation-delay:0.22s]" />
          <span className="h-4.75 w-1.5 animate-bob rounded-[3px] bg-line-strong [animation-delay:0.44s]" />
        </div>

        <h3 className="text-base font-semibold tracking-tight">Nothing tested yet</h3>
        <p className="mx-auto mt-2 max-w-[44ch] text-sm text-muted">
          Give it an address above. It opens the app the way a person would, tries the main flows,
          and comes back with what broke and what could be better.
        </p>
      </div>
    );
  }

  if (phase === 'error') {
    return (
      <div className={`${PANEL} ${NARROW} p-7`}>
        <h3 className="text-[15px] font-semibold tracking-tight text-critical">
          The test could not run
        </h3>
        <p className="mt-1.5 text-sm text-muted">{error ?? 'Something went wrong.'}</p>
      </div>
    );
  }

  if (phase === 'running' && steps.length) {
    return (
      <TestRunner
        steps={steps}
        live
        targetUrl={targetUrl}
        pinned={pinned}
        onPin={setPinned}
        startedAtMs={runStartedAt}
      />
    );
  }

  if (phase === 'running') {
    return (
      <div className={`${PANEL} ${NARROW} px-6.5 py-6`}>
        <div className="flex items-center gap-2.5">
          <span className="size-2 animate-ring rounded-full bg-accent" aria-hidden="true" />
          <h3 className="text-[15px] font-semibold tracking-tight">Working through the app</h3>
          <span className="ml-auto font-mono text-[14px] font-semibold text-muted tabular-nums">
            {formatDuration(elapsed)}
          </span>
        </div>

        <ol className="mt-4 grid gap-2.25" aria-live="polite">
          {progress.slice(-VISIBLE_STEPS).map((line, index, shown) => (
            <li
              key={progress.length - shown.length + index}
              className={`relative animate-rise pl-5 text-[13.5px] before:absolute before:top-1.75 before:left-1 before:size-1.5 before:rounded-full before:bg-current ${
                index === shown.length - 1 ? 'text-ink' : 'text-faint'
              }`}
            >
              {line}
            </li>
          ))}
        </ol>
        {progress.length > VISIBLE_STEPS ? (
          <p className="mt-3 pl-5 text-[12px] text-faint">{progress.length} steps so far</p>
        ) : null}
      </div>
    );
  }

  return (
    <div className="grid gap-5">
      {steps.length ? (
        <div ref={runnerRef} className="scroll-mt-6">
          <TestRunner
            steps={steps}
            live={false}
            targetUrl={result?.targetUrl ?? targetUrl}
            pinned={pinned}
            onPin={setPinned}
            durationMs={duration}
          />
        </div>
      ) : null}
      <div className={PANEL}>
        <header className="flex flex-wrap items-center justify-between gap-3.5 border-b border-line px-5 py-4.5">
          <div>
            <h3 className="text-[15px] font-semibold tracking-tight">
              {sorted.length} finding{sorted.length === 1 ? '' : 's'}
            </h3>
            <p className="mt-0.5 text-[12.5px] break-all text-faint">
              {result ? runStats(result) : null}
            </p>
          </div>

          <div
            className="flex gap-1 rounded-[9px] border border-line bg-raised p-0.75"
            role="group"
            aria-label="Filter findings"
          >
            {(
              [
                ['all', `All ${sorted.length}`],
                ['bug', `Broken ${bugCount}`],
                ['improvement', `Improvements ${improvementCount}`],
              ] as Array<[Filter, string]>
            ).map(([key, label]) => (
              <button
                key={key}
                type="button"
                className={`rounded-[7px] px-2.75 py-1.25 text-[12.5px] whitespace-nowrap transition ${
                  filter === key
                    ? 'bg-surface text-ink shadow-[var(--shadow-panel)]'
                    : 'text-muted hover:text-ink'
                }`}
                aria-pressed={filter === key}
                onClick={() => setFilter(key)}
              >
                {label}
              </button>
            ))}
          </div>
        </header>

        {result?.summary || result?.notes?.length || result?.id ? (
          <div className="grid gap-3 border-b border-line px-5 py-4">
            {result.summary ? (
              <p className="text-[14px] leading-relaxed text-ink">{result.summary}</p>
            ) : null}
            {result.notes?.map((note) => (
              <p key={note} className="text-[12.5px] text-medium">
                {note}
              </p>
            ))}
            {result.id ? <ReportActions scanId={result.id} /> : null}
          </div>
        ) : null}

        <div className="grid gap-2.5 p-3.5">
          {visible.length === 0 ? (
            <p className="p-6.5 text-center text-[13.5px] text-faint">Nothing in this category.</p>
          ) : (
            visible.map((finding) => {
              const step = stepFor(finding);
              return (
                <FindingCard
                  key={finding.id}
                  finding={finding}
                  onShowInReplay={step ? () => showInReplay(step) : undefined}
                />
              );
            })
          )}
        </div>

        {result?.suppressed?.length ? (
          <details className="border-t border-line px-5 py-4 text-[13px]">
            <summary className="cursor-pointer text-muted">
              Filtered out as duplicates or likely noise ({result.suppressed.length})
            </summary>
            <ul className="mt-3 grid gap-2">
              {result.suppressed.map(({ finding, reason }) => (
                <li key={finding.id} className="text-faint">
                  <span className="text-muted">{finding.title}</span>: {reason}
                </li>
              ))}
            </ul>
          </details>
        ) : null}
      </div>
    </div>
  );
}

function runStats(result: ScanResult): string {
  const parts = [`${result.pagesVisited} page${result.pagesVisited === 1 ? '' : 's'}`];
  if (result.agentSteps) parts.push(`${result.agentSteps} tool calls by Claude`);
  const took = durationBetween(result.startedAt, result.finishedAt);
  if (took !== null) parts.push(`took ${formatDuration(took)}`);
  const cost = result.usage?.estimatedCostUsd;
  if (cost != null) parts.push(`about $${cost.toFixed(2)} in API usage`);
  return `${parts.join(' · ')} on ${result.targetUrl}`;
}

function ReportActions({ scanId }: { scanId: string }) {
  const [copied, setCopied] = useState<'idle' | 'done' | 'failed'>('idle');

  async function copyMarkdown() {
    try {
      const response = await fetch(reportUrl(scanId));
      await navigator.clipboard.writeText(await response.text());
      setCopied('done');
    } catch {
      setCopied('failed');
    }
    setTimeout(() => setCopied('idle'), 2500);
  }

  const button =
    'rounded-[8px] border border-line-strong bg-surface px-3 py-1.5 text-[12.5px] text-muted transition hover:border-accent/40 hover:text-ink';
  return (
    <div className="flex flex-wrap gap-2">
      <button type="button" className={button} onClick={copyMarkdown}>
        {copied === 'done'
          ? 'Copied'
          : copied === 'failed'
            ? 'Copy failed'
            : 'Copy as Markdown issue'}
      </button>
      <a className={button} href={reportUrl(scanId)} target="_blank" rel="noreferrer">
        Open full report
      </a>
    </div>
  );
}
