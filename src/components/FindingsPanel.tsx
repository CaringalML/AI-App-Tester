import { useMemo, useState } from 'react';
import type { Category, ScanPhase, ScanResult } from '../lib/types';
import { SEVERITY_ORDER } from '../lib/types';
import { FindingCard } from './FindingCard';

interface Props {
  phase: ScanPhase;
  progress: string[];
  result: ScanResult | null;
  error: string | null;
}

type Filter = 'all' | Category;

const PANEL = 'rounded-[14px] border border-line bg-surface';

export function FindingsPanel({ phase, progress, result, error }: Props) {
  const [filter, setFilter] = useState<Filter>('all');

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
      <div className={`${PANEL} px-7 py-11 text-center`}>
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
      <div className={`${PANEL} p-7`}>
        <h3 className="text-[15px] font-semibold tracking-tight text-critical">
          The test could not run
        </h3>
        <p className="mt-1.5 text-sm text-muted">{error ?? 'Something went wrong.'}</p>
      </div>
    );
  }

  if (phase === 'running') {
    return (
      <div className={`${PANEL} px-6.5 py-6`}>
        <div className="flex items-center gap-2.5">
          <span className="size-2 animate-ring rounded-full bg-accent" aria-hidden="true" />
          <h3 className="text-[15px] font-semibold tracking-tight">Working through the app</h3>
        </div>

        <ol className="mt-4 grid gap-2.25">
          {progress.map((line, index) => (
            <li
              key={line}
              className={`relative animate-rise pl-5 text-[13.5px] before:absolute before:top-1.75 before:left-1 before:size-1.5 before:rounded-full before:bg-current ${
                index === progress.length - 1 ? 'text-ink' : 'text-faint'
              }`}
            >
              {line}
            </li>
          ))}
        </ol>
      </div>
    );
  }

  return (
    <div className={PANEL}>
      <header className="flex flex-wrap items-center justify-between gap-3.5 border-b border-line px-5 py-4.5">
        <div>
          <h3 className="text-[15px] font-semibold tracking-tight">
            {sorted.length} finding{sorted.length === 1 ? '' : 's'}
          </h3>
          <p className="mt-0.5 text-[12.5px] break-all text-faint">
            {result?.pagesVisited} pages checked on {result?.targetUrl}
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

      <div className="grid gap-2.5 p-3.5">
        {visible.length === 0 ? (
          <p className="p-6.5 text-center text-[13.5px] text-faint">Nothing in this category.</p>
        ) : (
          visible.map((finding) => <FindingCard key={finding.id} finding={finding} />)
        )}
      </div>
    </div>
  );
}
