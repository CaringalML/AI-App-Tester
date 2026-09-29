import { useEffect, useMemo, useRef, useState } from 'react';
import type { StepKind, TimelineStep } from '../lib/types';
import { formatDuration, useElapsed } from '../lib/time';

/*
 * A Cypress-style runner for a scan.
 *
 * Left: the command log, one row per step, with Claude's reason for each
 * action. Right: the browser as it looked at that step. While a scan runs the
 * viewport follows the latest step; hovering a row previews it and clicking
 * pins it ("time travel"). Element actions carry a frame from just before the
 * action with the target outlined, the way Cypress shows its hit box.
 */

interface Props {
  steps: TimelineStep[];
  live: boolean;
  targetUrl?: string;
  /** Pinned step index, or null to follow the latest step. */
  pinned: number | null;
  onPin: (index: number | null) => void;
  /** Client clock when the user pressed Run; drives the live timer. */
  startedAtMs?: number | null;
  /** Server-measured run length once the scan has finished. */
  durationMs?: number | null;
  /** All findings once the scan is done; while live, only Claude's are known. */
  findingsTotal?: number;
}

/* Literal class names so Tailwind generates them; see FindingCard for the same pattern. */
const KIND_STYLE: Record<StepKind, string> = {
  stage: 'text-faint',
  visit: 'text-low',
  navigate: 'text-low',
  back: 'text-muted',
  click: 'text-accent',
  type: 'text-improve',
  select: 'text-improve',
  press: 'text-muted',
  look: 'text-medium',
  read: 'text-faint',
  finding: 'text-critical',
};

const KIND_LABEL: Record<StepKind, string> = {
  stage: '',
  visit: 'visit',
  navigate: 'go to',
  back: 'back',
  click: 'click',
  type: 'type',
  select: 'select',
  press: 'press',
  look: 'look',
  read: 'read',
  finding: 'finding',
};

/** Swaps images only once the new one has loaded, so frames never flash blank. */
function useLoadedImage(src: string | null | undefined): string | null {
  const [shown, setShown] = useState<string | null>(src ?? null);
  useEffect(() => {
    if (!src) return;
    let cancelled = false;
    const image = new Image();
    image.onload = () => !cancelled && setShown(src);
    image.src = src;
    return () => {
      cancelled = true;
    };
  }, [src]);
  return src ? shown : null;
}

/** The most recent frame at or before a step, so rows without a picture still show the page. */
function frameFor(steps: TimelineStep[], position: number): TimelineStep | null {
  for (let i = position; i >= 0; i--) {
    if (steps[i]?.screenshotUrl) return steps[i];
  }
  return null;
}

function pathOf(url?: string | null): string {
  if (!url) return '';
  try {
    const parsed = new URL(url);
    return parsed.host + parsed.pathname;
  } catch {
    return url;
  }
}

export function TestRunner({
  steps,
  live,
  targetUrl,
  pinned,
  onPin,
  startedAtMs,
  durationMs,
  findingsTotal,
}: Props) {
  const [hovered, setHovered] = useState<number | null>(null);
  const [showBefore, setShowBefore] = useState(true);
  const [idleSeconds, setIdleSeconds] = useState(0);
  const logRef = useRef<HTMLOListElement>(null);

  const active = hovered ?? pinned ?? steps.length;
  const position = Math.min(Math.max(active - 1, 0), steps.length - 1);
  const step: TimelineStep | undefined = steps[position];
  const following = pinned === null && hovered === null;

  const hasBefore = Boolean(step?.beforeUrl);
  const useBefore = hasBefore && showBefore && !following;
  const frameStep = step?.screenshotUrl ? step : frameFor(steps, position);
  const src = useBefore ? step?.beforeUrl : frameStep?.screenshotUrl;
  const image = useLoadedImage(src);

  // Keep the newest row in view while following a live run.
  useEffect(() => {
    if (following && logRef.current) {
      logRef.current.scrollTo({ top: logRef.current.scrollHeight, behavior: 'smooth' });
    }
  }, [steps.length, following]);

  // Tick while live so the "thinking" hint appears during long model turns.
  const elapsed = useElapsed(startedAtMs, live);
  const firstAt = steps.length ? Date.parse(steps[0].at) : NaN;
  const lastStepAt = steps.length ? Date.parse(steps[steps.length - 1].at) : NaN;
  const runMs = live
    ? elapsed
    : (durationMs ?? (Number.isFinite(firstAt) ? lastStepAt - firstAt : 0));

  const lastAt = steps.at(-1)?.at;
  useEffect(() => {
    if (!live) return;
    const tick = () => setIdleSeconds(lastAt ? (Date.now() - Date.parse(lastAt)) / 1000 : 0);
    tick();
    const timer = setInterval(tick, 500);
    return () => clearInterval(timer);
  }, [live, lastAt]);

  // A newly pinned element action opens on its "before" frame, where the outline applies.
  useEffect(() => setShowBefore(true), [pinned]);

  const counts = useMemo(() => {
    const actions = steps.filter((s) => !['stage', 'visit', 'read', 'finding'].includes(s.kind));
    return {
      actions: actions.length,
      failed: actions.filter((s) => s.status === 'failed').length,
      findings: steps.filter((s) => s.kind === 'finding').length,
    };
  }, [steps]);

  const claudeTurn = live && idleSeconds > 2.5 && steps.some((s) => s.kind !== 'stage');

  return (
    <div className="grid grid-cols-[minmax(0,1fr)] overflow-hidden rounded-[14px] border border-line bg-surface @4xl:grid-cols-[minmax(0,22rem)_minmax(0,1fr)]">
      {/* Command log */}
      <section className="order-2 flex min-h-0 flex-col border-t border-line @4xl:order-1 @4xl:border-t-0 @4xl:border-r">
        <header className="flex items-center justify-between gap-3 border-b border-line px-4 py-3">
          <div className="flex items-center gap-2">
            <h3 className="text-[13px] font-semibold tracking-tight">Command log</h3>
            {live ? (
              <span className="flex items-center gap-1.5 rounded-full border border-critical/40 px-2 py-0.5 text-[10.5px] font-semibold tracking-wide text-critical uppercase">
                <span className="size-1.5 animate-pulse rounded-full bg-critical" />
                Live
              </span>
            ) : null}
          </div>
          <div
            className={`flex items-center gap-1.5 font-mono text-[15px] font-semibold tabular-nums ${
              live ? 'text-ink' : 'text-muted'
            }`}
            title={live ? 'Time since the test started' : 'Total time the test took'}
            aria-live="off"
          >
            <span className={live ? 'text-accent' : 'text-faint'}>
              <svg
                className="size-3.5"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth={2}
                strokeLinecap="round"
                aria-hidden="true"
              >
                <circle cx="12" cy="13" r="8" />
                <path d="M12 9v4l2.5 2.5M9 2h6" />
              </svg>
            </span>
            {formatDuration(runMs)}
          </div>
        </header>

        <div className="flex gap-2.5 border-b border-line px-4 py-1.5 font-mono text-[11px] text-faint">
          <span className="flex-1">
            {live ? 'Elapsed' : 'Finished in ' + formatDuration(runMs)}
          </span>
          <span title="Browser actions">{counts.actions} actions</span>
          {counts.failed ? (
            <span className="text-critical" title="Actions that failed">
              {counts.failed} failed
            </span>
          ) : null}
          <span className="text-medium" title="Findings reported">
            {findingsTotal ?? counts.findings} found
          </span>
        </div>

        <ol
          ref={logRef}
          className="max-h-80 min-h-0 flex-1 overflow-y-auto py-1.5 @4xl:max-h-[34rem]"
          onMouseLeave={() => setHovered(null)}
        >
          {steps.map((s) => {
            const selected = s.index === (pinned ?? -1);
            const current = following && s.index === steps.length;
            const stage = s.kind === 'stage';
            return (
              <li key={s.index}>
                <button
                  type="button"
                  onMouseEnter={() => setHovered(s.index)}
                  onClick={() => onPin(selected ? null : s.index)}
                  className={`group flex w-full gap-2.5 border-l-2 px-3.5 py-1.5 text-left transition ${
                    selected
                      ? 'border-accent bg-accent/10'
                      : s.status === 'failed'
                        ? 'border-critical/70 hover:bg-raised'
                        : s.kind === 'finding'
                          ? 'border-medium/70 bg-medium/5 hover:bg-medium/10'
                          : current
                            ? 'border-line-strong bg-raised'
                            : 'border-transparent hover:bg-raised'
                  }`}
                >
                  <span className="w-5 flex-none pt-px text-right font-mono text-[10.5px] text-faint">
                    {s.index}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="flex items-baseline gap-2">
                      {KIND_LABEL[s.kind] ? (
                        <span
                          className={`flex-none font-mono text-[10.5px] font-semibold tracking-wide uppercase ${KIND_STYLE[s.kind]}`}
                        >
                          {KIND_LABEL[s.kind]}
                        </span>
                      ) : null}
                      <span
                        className={`min-w-0 truncate ${
                          stage ? 'text-[12px] text-faint italic' : 'text-[12.5px] text-ink'
                        }`}
                        title={s.label}
                      >
                        {s.label}
                      </span>
                    </span>
                    {s.why ? (
                      <span className="mt-0.5 block text-[11.5px] leading-snug text-muted">
                        {s.why}
                      </span>
                    ) : null}
                    {s.status === 'failed' ? (
                      <span className="mt-0.5 block text-[11px] text-critical">
                        Action did not complete
                      </span>
                    ) : null}
                  </span>
                  <span
                    className="flex-none self-start pt-px font-mono text-[10.5px] text-faint tabular-nums"
                    title="Time since the test started"
                  >
                    {Number.isFinite(firstAt) ? formatDuration(Date.parse(s.at) - firstAt) : ''}
                  </span>
                  {s.signals ? (
                    <span
                      className="flex-none self-start rounded-full border border-critical/40 px-1.5 font-mono text-[10px] text-critical"
                      title="New errors, failed requests or dialogs after this step"
                    >
                      {s.signals}
                    </span>
                  ) : null}
                </button>
              </li>
            );
          })}
          {live && steps.length === 0 ? (
            <li className="px-4 py-3 text-[12.5px] text-faint">Starting the browser…</li>
          ) : null}
        </ol>
      </section>

      {/* Browser viewport */}
      <section className="order-1 flex min-w-0 flex-col @4xl:order-2">
        <div className="flex items-center gap-3 border-b border-line bg-raised px-3.5 py-2.5">
          <span className="flex gap-1.5" aria-hidden="true">
            <span className="size-2.5 rounded-full bg-line-strong" />
            <span className="size-2.5 rounded-full bg-line-strong" />
            <span className="size-2.5 rounded-full bg-line-strong" />
          </span>
          <span className="min-w-0 flex-1 truncate rounded-md border border-line bg-bg px-2.5 py-1 font-mono text-[11.5px] text-muted">
            {pathOf(step?.url ?? frameStep?.url ?? targetUrl) || 'about:blank'}
          </span>
          {hasBefore && !following ? (
            <span className="flex flex-none rounded-md border border-line bg-bg p-0.5 text-[11px]">
              {(['before', 'after'] as const).map((which) => (
                <button
                  key={which}
                  type="button"
                  onClick={() => setShowBefore(which === 'before')}
                  className={`rounded px-2 py-0.5 capitalize transition ${
                    (which === 'before') === showBefore
                      ? 'bg-surface text-ink shadow-[var(--shadow-panel)]'
                      : 'text-faint hover:text-ink'
                  }`}
                >
                  {which}
                </button>
              ))}
            </span>
          ) : null}
        </div>

        <div className="relative aspect-[16/10] w-full overflow-hidden bg-bg">
          {image ? (
            <img
              src={image}
              alt={step ? `Page at step ${step.index}: ${step.label}` : 'Page under test'}
              className="absolute inset-0 size-full object-cover object-top"
            />
          ) : (
            <div className="absolute inset-0 grid place-items-center text-[13px] text-faint">
              {live ? 'Opening the page…' : 'No screenshots were recorded for this run.'}
            </div>
          )}

          {useBefore && step?.box ? (
            <span
              className="pointer-events-none absolute rounded-[3px] border-2 border-accent bg-accent/15 shadow-[0_0_0_4px_color-mix(in_oklab,var(--accent)_25%,transparent)]"
              style={{
                left: `${step.box.x * 100}%`,
                top: `${step.box.y * 100}%`,
                width: `${Math.max(step.box.w * 100, 1)}%`,
                height: `${Math.max(step.box.h * 100, 1.5)}%`,
              }}
            />
          ) : null}

          {claudeTurn && following ? (
            <div className="absolute inset-x-0 bottom-0 flex items-center gap-2 bg-linear-to-t from-black/70 to-transparent px-4 pt-8 pb-3 text-[12.5px] text-white">
              <span className="size-2 animate-ring rounded-full bg-accent" />
              Claude is deciding what to try next…
            </div>
          ) : null}
        </div>

        <footer className="flex min-h-12 items-center justify-between gap-3 border-t border-line px-4 py-2.5 text-[12px]">
          <p className="min-w-0 text-muted">
            {step ? (
              <>
                <span className="font-mono text-faint">#{step.index}</span> {step.why ?? step.label}
              </>
            ) : (
              'Waiting for the first step'
            )}
          </p>
          {pinned !== null ? (
            <button
              type="button"
              onClick={() => onPin(null)}
              className="flex-none rounded-md border border-line-strong px-2.5 py-1 text-[11.5px] text-muted transition hover:border-accent/40 hover:text-ink"
            >
              {live ? 'Back to live' : 'Show last step'}
            </button>
          ) : (
            <span className="hidden flex-none text-[11.5px] text-faint sm:inline">
              {live
                ? 'Following live · click a step to pin it'
                : 'Hover or click a step to replay it'}
            </span>
          )}
        </footer>
      </section>
    </div>
  );
}
