import { useState } from 'react';
import type { StepKind, TimelineStep } from '../lib/types';

/*
 * The command log as a GitHub Actions style run: steps grouped into phases,
 * each with a status icon on a connected timeline and its duration. While a
 * test runs, the current step spins and phases not reached yet sit queued, so
 * the log reads as a loading timeline.
 */

type PhaseId = 'prepare' | 'explore' | 'review';
type Status = 'running' | 'success' | 'failed' | 'warning' | 'finding' | 'pending' | 'skipped';

const PHASES: { id: PhaseId; label: string }[] = [
  { id: 'prepare', label: 'Prepare' },
  { id: 'explore', label: 'Explore with Claude' },
  { id: 'review', label: 'Review findings' },
];

/* Literal class names so Tailwind generates them. */
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

/** GitHub-style durations: "0s", "12s", "1m 5s". */
function short(ms: number): string {
  const seconds = Math.max(0, Math.round(ms / 1000));
  return seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

function StatusIcon({ status, large = false }: { status: Status; large?: boolean }) {
  const size = large ? 'size-4' : 'size-3.5';
  switch (status) {
    case 'running':
      return (
        <span
          className={`block ${size} animate-spin rounded-full border-2 border-medium border-t-transparent`}
          aria-label="In progress"
        />
      );
    case 'pending':
      return (
        <span
          className={`block ${size} rounded-full border-2 border-line-strong`}
          aria-label="Queued"
        />
      );
    case 'skipped':
      return (
        <svg className={`${size} text-faint`} viewBox="0 0 16 16" aria-label="Skipped">
          <circle cx="8" cy="8" r="6.5" fill="none" stroke="currentColor" strokeWidth="1.5" />
          <path d="M4 12L12 4" stroke="currentColor" strokeWidth="1.5" />
        </svg>
      );
    case 'finding':
      return (
        <svg className={`${size} text-critical`} viewBox="0 0 16 16" aria-label="Finding">
          <circle cx="8" cy="8" r="8" fill="currentColor" />
          <path
            d="M5.5 12V4.2h4.4l-.9 1.9.9 1.9H5.5"
            fill="none"
            stroke="#fff"
            strokeWidth="1.4"
            strokeLinejoin="round"
          />
        </svg>
      );
    case 'failed':
      return (
        <svg className={`${size} text-critical`} viewBox="0 0 16 16" aria-label="Failed">
          <circle cx="8" cy="8" r="8" fill="currentColor" />
          <path
            d="M5.2 5.2l5.6 5.6M10.8 5.2l-5.6 5.6"
            stroke="#fff"
            strokeWidth="1.7"
            strokeLinecap="round"
          />
        </svg>
      );
    case 'warning':
      return (
        <svg className={`${size} text-medium`} viewBox="0 0 16 16" aria-label="Warning">
          <circle cx="8" cy="8" r="8" fill="currentColor" />
          <path d="M8 4.2v4.6M8 11.3v.3" stroke="#fff" strokeWidth="1.8" strokeLinecap="round" />
        </svg>
      );
    default:
      return (
        <svg className={`${size} text-success`} viewBox="0 0 16 16" aria-label="Done">
          <circle cx="8" cy="8" r="8" fill="currentColor" />
          <path
            d="M4.6 8.3l2.2 2.2 4.6-4.8"
            fill="none"
            stroke="#fff"
            strokeWidth="1.8"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      );
  }
}

interface Props {
  steps: TimelineStep[];
  live: boolean;
  pinned: number | null;
  following: boolean;
  /** Current time while live, so the running step's duration ticks. */
  now: number;
  onPin: (index: number | null) => void;
  onHover: (index: number | null) => void;
}

export function StepTimeline({ steps, live, pinned, following, now, onPin, onHover }: Props) {
  const [collapsed, setCollapsed] = useState<Set<PhaseId>>(new Set());

  const at = (s: TimelineStep) => Date.parse(s.at);
  const last = steps[steps.length - 1];
  const currentPhase: PhaseId | null = live && last ? (last.phase ?? 'prepare') : null;
  const order = (id: PhaseId) => PHASES.findIndex((p) => p.id === id);

  const groups = PHASES.map((phase) => ({
    ...phase,
    steps: steps.filter((s) => (s.phase ?? 'prepare') === phase.id),
  }));

  function stepStatus(s: TimelineStep): Status {
    if (live && s === last) return 'running';
    if (s.status === 'failed') return 'failed';
    if (s.kind === 'finding') return 'finding';
    if (s.status === 'warning') return 'warning';
    return 'success';
  }

  function phaseStatus(id: PhaseId, count: number): Status {
    if (count === 0) {
      if (!live) return 'skipped';
      return currentPhase && order(id) > order(currentPhase) ? 'pending' : 'skipped';
    }
    return live && id === currentPhase ? 'running' : 'success';
  }

  function toggle(id: PhaseId) {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  return (
    <div className="py-1">
      {groups.map((group, gi) => {
        const status = phaseStatus(group.id, group.steps.length);
        // A phase starts at its first step and ends where the next phase begins.
        const nextStart = groups.slice(gi + 1).find((g) => g.steps.length)?.steps[0];
        const start = group.steps[0] ? at(group.steps[0]) : NaN;
        const end = nextStart ? at(nextStart) : live ? now : last ? at(last) : NaN;
        const isCollapsed = collapsed.has(group.id);
        const queued = status === 'pending';

        return (
          <section key={group.id} aria-label={group.label}>
            <button
              type="button"
              onClick={() => group.steps.length && toggle(group.id)}
              aria-expanded={group.steps.length ? !isCollapsed : undefined}
              className={`flex w-full items-center gap-1.5 border-l-2 border-transparent py-2 pr-3.5 pl-2.5 text-left transition ${
                group.steps.length ? 'hover:bg-raised' : 'cursor-default'
              }`}
            >
              <svg
                className={`size-3 flex-none text-faint transition-transform ${
                  isCollapsed ? '' : 'rotate-90'
                } ${group.steps.length ? '' : 'opacity-0'}`}
                viewBox="0 0 16 16"
                aria-hidden="true"
              >
                <path
                  d="M6 4l4 4-4 4"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.8"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
              <StatusIcon status={status} large />
              <span
                className={`min-w-0 flex-1 truncate text-[12.5px] font-semibold ${
                  queued || status === 'skipped' ? 'text-faint' : 'text-ink'
                }`}
              >
                {group.label}
              </span>
              <span className="flex-none font-mono text-[10.5px] text-faint tabular-nums">
                {queued
                  ? 'queued'
                  : Number.isFinite(start) && Number.isFinite(end)
                    ? short(end - start)
                    : ''}
              </span>
            </button>

            {group.steps.length && !isCollapsed ? (
              <ol className="relative">
                {/* The connector that runs through the step icons. */}
                <span
                  className="absolute top-3 bottom-3 left-[37.5px] w-px bg-line-strong"
                  aria-hidden="true"
                />
                {group.steps.map((s) => {
                  const position = steps.indexOf(s);
                  const next = steps[position + 1];
                  const duration = next ? at(next) - at(s) : live ? now - at(s) : null;
                  const selected = s.index === (pinned ?? -1);
                  const current = live && following && s === last;
                  const stage = s.kind === 'stage';
                  return (
                    <li key={s.index} className="relative">
                      <button
                        type="button"
                        onMouseEnter={() => onHover(s.index)}
                        onClick={() => onPin(selected ? null : s.index)}
                        className={`flex w-full items-start gap-2.5 border-l-2 py-1.5 pr-3.5 pl-7 text-left transition ${
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
                        {/* Solid backing so the connector line passes behind the icon. */}
                        <span className="relative z-10 mt-0.5 grid w-4 flex-none place-items-center rounded-full bg-surface">
                          <StatusIcon status={stepStatus(s)} />
                        </span>
                        <span
                          className="w-5 flex-none pt-px text-right font-mono text-[10.5px] text-faint tabular-nums"
                          aria-label={`Step ${s.index}`}
                        >
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
                                stage ? 'text-[12px] text-muted' : 'text-[12.5px] text-ink'
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
                        {s.signals ? (
                          <span
                            className="flex-none self-start rounded-full border border-critical/40 px-1.5 font-mono text-[10px] text-critical"
                            title="New errors, failed requests or dialogs after this step"
                          >
                            {s.signals}
                          </span>
                        ) : null}
                        <span
                          className="w-9 flex-none self-start pt-px text-right font-mono text-[10.5px] text-faint tabular-nums"
                          title="How long this step took"
                        >
                          {duration !== null ? short(duration) : ''}
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ol>
            ) : null}
          </section>
        );
      })}
      {live && steps.length === 0 ? (
        <p className="px-4 py-3 text-[12.5px] text-faint">Starting the browser…</p>
      ) : null}
    </div>
  );
}
