import { useState } from 'react';
import type { HistoryEntry } from '../lib/history';
import type { ScanSummary } from '../lib/types';
import {
  durationBetween,
  formatAgo,
  formatDuration,
  formatFullDate,
  formatWhen,
} from '../lib/time';
import { ConfirmDialog } from './ConfirmDialog';

interface Props {
  entries: HistoryEntry[];
  summaries: Record<string, ScanSummary>;
  activeId: string | null;
  onOpen: (id: string) => void;
  onNewTest: () => void;
  onDelete: (id: string) => Promise<void>;
  /**
   * expanded / collapsed: the desktop rail, full list or icon strip.
   * drawer: the overlay on small screens, which is dismissed rather than collapsed.
   */
  mode: 'expanded' | 'collapsed' | 'drawer';
  /** Collapses or expands the rail; closes the drawer. */
  onToggle: () => void;
}

const SHORTCUT =
  typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform) ? '⌘B' : 'Ctrl+B';

function splitUrl(url: string): { host: string; path: string } {
  try {
    const parsed = new URL(url);
    return { host: parsed.host.replace(/^www\./, ''), path: parsed.pathname };
  } catch {
    return { host: url, path: '' };
  }
}

const ICON = {
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 2,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
  viewBox: '0 0 24 24',
  'aria-hidden': true,
};

export function HistorySidebar({
  entries,
  summaries,
  activeId,
  onOpen,
  onNewTest,
  onDelete,
  mode,
  onToggle,
}: Props) {
  /** What the confirmation dialog is about to delete, if it is open. */
  const [pending, setPending] = useState<{ ids: string[]; all: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const deletable = entries.filter((e) => {
    const status = summaries[e.id]?.status;
    return status === 'done' || status === 'error';
  });

  function ask(ids: string[], all: boolean) {
    setError(null);
    setPending({ ids, all });
  }

  function cancel() {
    if (busy) return;
    setPending(null);
    setError(null);
  }

  async function confirmDelete() {
    if (!pending) return;
    setBusy(true);
    setError(null);
    const failures: string[] = [];
    // One at a time: clearing a long history should not burst the API.
    for (const id of pending.ids) {
      try {
        await onDelete(id);
      } catch (cause) {
        failures.push(cause instanceof Error ? cause.message : 'Could not delete.');
      }
    }
    setBusy(false);
    if (!failures.length) {
      setPending(null);
      return;
    }
    // Keep the dialog open and say what happened; the ones that worked are gone.
    setPending((prev) =>
      prev ? { ...prev, ids: prev.ids.filter((id) => entries.some((e) => e.id === id)) } : prev,
    );
    setError(
      pending.ids.length === 1
        ? failures[0]
        : `${failures.length} of ${pending.ids.length} could not be deleted. ${failures[0]}`,
    );
  }

  const target = pending && !pending.all ? entries.find((e) => e.id === pending.ids[0]) : null;
  const targetSummary = target ? summaries[target.id] : undefined;
  const targetUrl = target ? splitUrl(targetSummary?.targetUrl ?? target.targetUrl) : null;

  const dialog = (
    <ConfirmDialog
      open={pending !== null}
      title={pending?.all ? `Delete ${pending.ids.length} tests?` : 'Delete this test?'}
      confirmLabel={pending?.all ? 'Delete all' : 'Delete'}
      busy={busy}
      error={error}
      onConfirm={confirmDelete}
      onCancel={cancel}
    >
      {target && targetUrl ? (
        <div className="mb-3 flex items-center gap-3 rounded-lg border border-line bg-bg p-2">
          <span className="h-9 w-14 flex-none overflow-hidden rounded-md border border-line bg-surface">
            {targetSummary?.thumbnailUrl ? (
              <img
                src={targetSummary.thumbnailUrl}
                alt=""
                className="size-full object-cover object-top"
              />
            ) : null}
          </span>
          <span className="min-w-0">
            <span className="block truncate text-[12.5px] font-medium text-ink">
              {targetUrl.host}
            </span>
            <span className="block truncate font-mono text-[11px] text-faint">
              {targetUrl.path} ·{' '}
              {formatWhen(targetSummary?.startedAt || targetSummary?.createdAt || target.createdAt)}
            </span>
          </span>
        </div>
      ) : null}
      {pending?.all
        ? 'Every finished test in this browser, its findings and all of its screenshots will be removed from the server. Tests still running are kept.'
        : 'Its findings, replay and every screenshot will be removed from the server.'}{' '}
      <strong className="font-medium text-ink">This cannot be undone.</strong>
    </ConfirmDialog>
  );

  const collapsed = mode === 'collapsed';

  /*
   * The toggle sits in the same place in both states, top-left of the rail,
   * the way modern app sidebars behave. The drawer on small screens has a
   * close button instead, since it is dismissed rather than collapsed.
   */
  const toggle =
    mode === 'drawer' ? null : (
      <button
        type="button"
        onClick={onToggle}
        aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        aria-expanded={!collapsed}
        title={`${collapsed ? 'Expand' : 'Collapse'} sidebar (${SHORTCUT})`}
        className="grid size-9 flex-none place-items-center rounded-lg text-muted transition hover:bg-raised hover:text-ink"
      >
        <svg className="size-4.5" {...ICON}>
          <rect x="3" y="4" width="18" height="16" rx="2" />
          <path d="M9 4v16" />
        </svg>
      </button>
    );

  if (collapsed) {
    return (
      <aside className="flex h-full w-full flex-col border-r border-line bg-surface">
        <header className="flex h-15 flex-none items-center border-b border-line px-2.5">
          {toggle}
        </header>
        <div className="flex flex-none flex-col items-start gap-1 border-b border-line px-2.5 py-2">
          <button
            type="button"
            onClick={onNewTest}
            aria-label="New test"
            title="New test"
            className="grid size-9 place-items-center rounded-lg text-muted transition hover:bg-raised hover:text-ink"
          >
            <svg className="size-4.5" {...ICON}>
              <path d="M12 5v14M5 12h14" />
            </svg>
          </button>
        </div>
        <ol className="flex min-h-0 flex-1 flex-col items-start gap-1.5 overflow-y-auto px-2.5 py-2">
          {entries.map((entry) => {
            const summary = summaries[entry.id];
            const { host, path } = splitUrl(summary?.targetUrl ?? entry.targetUrl);
            const running = summary?.status === 'running' || summary?.status === 'queued';
            const active = entry.id === activeId;
            return (
              <li key={entry.id}>
                <button
                  type="button"
                  onClick={() => onOpen(entry.id)}
                  aria-label={`Open the test of ${host}${path}`}
                  title={`${host}${path}\n${formatWhen(
                    summary?.startedAt || summary?.createdAt || entry.createdAt,
                  )}${
                    summary && !running
                      ? ` · ${summary.bugs} broken, ${summary.improvements} to improve`
                      : running
                        ? ' · running'
                        : ''
                  }`}
                  className={`relative grid size-9 place-items-center overflow-hidden rounded-lg border bg-bg transition ${
                    active
                      ? 'border-accent ring-2 ring-accent/30'
                      : 'border-line hover:border-line-strong'
                  }`}
                >
                  {summary?.thumbnailUrl ? (
                    <img
                      src={summary.thumbnailUrl}
                      alt=""
                      loading="lazy"
                      className="size-full object-cover object-top"
                    />
                  ) : (
                    <span className="font-mono text-[12px] text-faint uppercase">
                      {host.charAt(0)}
                    </span>
                  )}
                  {running ? (
                    <span className="absolute top-0.5 right-0.5 size-2 animate-pulse rounded-full bg-accent ring-2 ring-surface" />
                  ) : summary?.bugs ? (
                    <span className="absolute top-0.5 right-0.5 size-2 rounded-full bg-critical ring-2 ring-surface" />
                  ) : null}
                </button>
              </li>
            );
          })}
        </ol>
        {dialog}
      </aside>
    );
  }

  return (
    <aside className="flex h-full w-full flex-col border-r border-line bg-surface">
      <header className="flex h-15 flex-none items-center gap-1.5 border-b border-line px-2.5">
        {toggle}
        <div className={`min-w-0 flex-1 ${mode === 'drawer' ? 'pl-1.5' : ''}`}>
          <h2 className="text-[13.5px] font-semibold tracking-tight">Test history</h2>
          <p className="text-[11.5px] text-faint">Saved in this browser</p>
        </div>
        <button
          type="button"
          onClick={onNewTest}
          className="flex flex-none items-center gap-1 rounded-md border border-line-strong px-2 py-1 text-[12px] text-muted transition hover:border-accent/40 hover:text-ink"
        >
          <svg className="size-3.5" {...ICON}>
            <path d="M12 5v14M5 12h14" />
          </svg>
          New
        </button>
        {mode === 'drawer' ? (
          <button
            type="button"
            onClick={onToggle}
            aria-label="Close history"
            className="grid size-8 flex-none place-items-center rounded-md text-muted transition hover:bg-raised hover:text-ink"
          >
            <svg className="size-4" {...ICON}>
              <path d="M6 6l12 12M18 6L6 18" />
            </svg>
          </button>
        ) : null}
      </header>

      <ol className="min-h-0 flex-1 overflow-y-auto p-2">
        {entries.length === 0 ? (
          <li className="px-3 py-8 text-center text-[12.5px] leading-relaxed text-faint">
            Tests you run appear here, so you can reopen their replay and findings later.
          </li>
        ) : null}

        {entries.map((entry) => {
          const summary = summaries[entry.id];
          const { host, path } = splitUrl(summary?.targetUrl ?? entry.targetUrl);
          const status = summary?.status;
          const running = status === 'running' || status === 'queued';
          const active = entry.id === activeId;
          const took = durationBetween(summary?.startedAt, summary?.finishedAt);
          const when = summary?.startedAt || summary?.createdAt || entry.createdAt;

          return (
            <li key={entry.id} className="group relative">
              <button
                type="button"
                onClick={() => onOpen(entry.id)}
                className={`flex w-full gap-3 rounded-[10px] p-2 text-left transition disabled:opacity-50 ${
                  active ? 'bg-accent/10 ring-1 ring-accent/30' : 'hover:bg-raised'
                }`}
              >
                <span className="relative h-10 w-16 flex-none overflow-hidden rounded-md border border-line bg-bg">
                  {summary?.thumbnailUrl ? (
                    <img
                      src={summary.thumbnailUrl}
                      alt=""
                      loading="lazy"
                      className="size-full object-cover object-top"
                    />
                  ) : (
                    <span className="grid size-full place-items-center font-mono text-[13px] text-faint uppercase">
                      {host.charAt(0)}
                    </span>
                  )}
                </span>

                <span className="min-w-0 flex-1 pr-6">
                  <span className="block truncate text-[12.5px] font-medium text-ink">{host}</span>
                  <span className="block truncate font-mono text-[10.5px] text-faint">{path}</span>
                  <span className="mt-1 flex flex-wrap items-center gap-x-2 text-[11px]">
                    {running ? (
                      <span className="flex items-center gap-1 text-accent">
                        <span className="size-1.5 animate-pulse rounded-full bg-accent" />
                        Running
                      </span>
                    ) : status === 'error' && !summary?.bugs && !summary?.improvements ? (
                      <span className="text-critical">Failed</span>
                    ) : summary ? (
                      <>
                        <span className={summary.bugs ? 'text-critical' : 'text-faint'}>
                          {summary.bugs} broken
                        </span>
                        <span className="text-improve">{summary.improvements} to improve</span>
                      </>
                    ) : (
                      <span className="text-faint">Loading…</span>
                    )}
                  </span>
                  <span className="mt-0.5 flex flex-wrap items-center gap-x-1 text-[10.5px] text-faint">
                    <svg className="size-3 flex-none" {...ICON}>
                      <circle cx="12" cy="12" r="9" />
                      <path d="M12 7v5l3 2" />
                    </svg>
                    <time
                      dateTime={when}
                      title={`${formatFullDate(when)} (${formatAgo(when)})`}
                      className="whitespace-nowrap"
                    >
                      {formatWhen(when)}
                    </time>
                    {took !== null ? (
                      <span className="whitespace-nowrap">· took {formatDuration(took)}</span>
                    ) : null}
                  </span>
                </span>
              </button>

              {!running ? (
                <button
                  type="button"
                  onClick={() => ask([entry.id], false)}
                  aria-label={`Delete the test of ${host}`}
                  title="Delete this test and its screenshots"
                  className="absolute top-2 right-2 grid size-6 place-items-center rounded-md text-faint opacity-0 transition group-hover:opacity-100 hover:bg-critical/10 hover:text-critical focus-visible:opacity-100"
                >
                  <svg className="size-3.5" {...ICON}>
                    <path d="M4 7h16M10 11v6M14 11v6M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4h6v3" />
                  </svg>
                </button>
              ) : null}
            </li>
          );
        })}
      </ol>

      {deletable.length > 1 ? (
        <footer className="border-t border-line p-3">
          <button
            type="button"
            onClick={() =>
              ask(
                deletable.map((e) => e.id),
                true,
              )
            }
            className="w-full rounded-md px-2 py-1.5 text-[12px] text-faint transition hover:bg-critical/10 hover:text-critical"
          >
            Delete all finished tests
          </button>
        </footer>
      ) : null}
      {dialog}
    </aside>
  );
}
