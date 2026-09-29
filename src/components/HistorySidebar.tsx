import { useState } from 'react';
import type { HistoryEntry } from '../lib/history';
import type { ScanSummary } from '../lib/types';
import { durationBetween, formatAgo, formatDuration } from '../lib/time';

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
  const [confirming, setConfirming] = useState<string | 'all' | null>(null);
  const [deleting, setDeleting] = useState<Set<string>>(new Set());
  const [failures, setFailures] = useState<Record<string, string>>({});

  const deletable = entries.filter((e) => {
    const status = summaries[e.id]?.status;
    return status === 'done' || status === 'error';
  });

  async function remove(ids: string[]) {
    setConfirming(null);
    setDeleting((prev) => new Set([...prev, ...ids]));
    // One at a time: a clear-all of a busy history should not burst the API.
    for (const id of ids) {
      try {
        await onDelete(id);
        setFailures(({ [id]: _, ...rest }) => rest);
      } catch (cause) {
        setFailures((prev) => ({
          ...prev,
          [id]: cause instanceof Error ? cause.message : 'Could not delete.',
        }));
      }
      setDeleting((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    }
  }

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
                  title={`${host}${path}${
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
          const busy = deleting.has(entry.id);
          const took = durationBetween(summary?.startedAt, summary?.finishedAt);

          return (
            <li key={entry.id} className="group relative">
              <button
                type="button"
                onClick={() => onOpen(entry.id)}
                disabled={busy}
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
                  <span className="mt-0.5 block text-[10.5px] text-faint">
                    {formatAgo(summary?.createdAt ?? entry.createdAt)}
                    {took !== null ? ` · took ${formatDuration(took)}` : ''}
                  </span>
                </span>
              </button>

              {!running ? (
                <button
                  type="button"
                  onClick={() => setConfirming(entry.id)}
                  disabled={busy}
                  aria-label={`Delete the test of ${host}`}
                  title="Delete this test and its screenshots"
                  className="absolute top-2 right-2 grid size-6 place-items-center rounded-md text-faint opacity-0 transition group-hover:opacity-100 hover:bg-critical/10 hover:text-critical focus-visible:opacity-100"
                >
                  {busy ? (
                    <span className="size-3 animate-spin-fast rounded-full border-2 border-current border-r-transparent" />
                  ) : (
                    <svg className="size-3.5" {...ICON}>
                      <path d="M4 7h16M10 11v6M14 11v6M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4h6v3" />
                    </svg>
                  )}
                </button>
              ) : null}

              {confirming === entry.id ? (
                <div className="mx-2 mb-2 rounded-lg border border-critical/40 bg-critical/5 p-2.5 text-[12px]">
                  <p className="text-ink">
                    Delete this test and its screenshots? This cannot be undone.
                  </p>
                  <div className="mt-2 flex gap-2">
                    <button
                      type="button"
                      onClick={() => remove([entry.id])}
                      className="rounded-md bg-critical px-2.5 py-1 font-medium text-white hover:brightness-110"
                    >
                      Delete
                    </button>
                    <button
                      type="button"
                      onClick={() => setConfirming(null)}
                      className="rounded-md border border-line-strong px-2.5 py-1 text-muted hover:text-ink"
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              ) : null}

              {failures[entry.id] ? (
                <p className="mx-2 mb-2 text-[11.5px] text-critical">{failures[entry.id]}</p>
              ) : null}
            </li>
          );
        })}
      </ol>

      {deletable.length > 1 ? (
        <footer className="border-t border-line p-3">
          {confirming === 'all' ? (
            <div className="text-[12px]">
              <p className="text-ink">
                Delete {deletable.length} finished tests and all their screenshots?
              </p>
              <div className="mt-2 flex gap-2">
                <button
                  type="button"
                  onClick={() => remove(deletable.map((e) => e.id))}
                  className="rounded-md bg-critical px-2.5 py-1 font-medium text-white hover:brightness-110"
                >
                  Delete all
                </button>
                <button
                  type="button"
                  onClick={() => setConfirming(null)}
                  className="rounded-md border border-line-strong px-2.5 py-1 text-muted hover:text-ink"
                >
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <button
              type="button"
              onClick={() => setConfirming('all')}
              className="w-full rounded-md px-2 py-1.5 text-[12px] text-faint transition hover:bg-critical/10 hover:text-critical"
            >
              Delete all finished tests
            </button>
          )}
        </footer>
      ) : null}
    </aside>
  );
}
