import { useCallback, useEffect, useRef, useState } from 'react';
import { ScanForm } from './components/ScanForm';
import { FindingsPanel } from './components/FindingsPanel';
import { HistorySidebar } from './components/HistorySidebar';
import { ThemeToggle } from './components/ThemeToggle';
import { DEFAULT_OPTIONS } from './lib/types';
import type { ScanOptions, ScanPhase, ScanResult, ScanSummary, TimelineStep } from './lib/types';
import { runMockScan } from './lib/mockScan';
import {
  deleteScan,
  fetchHistory,
  followScan,
  isLiveApi,
  startScan,
  type LiveUpdate,
} from './lib/api';
import { loadHistory, saveHistory, type HistoryEntry } from './lib/history';

const HISTORY_REFRESH_MS = 8000;
const RAIL_KEY = 'ai-app-tester:sidebar';

export default function App() {
  const [options, setOptions] = useState<ScanOptions>(DEFAULT_OPTIONS);
  const [phase, setPhase] = useState<ScanPhase>('idle');
  const [progress, setProgress] = useState<string[]>([]);
  const [result, setResult] = useState<ScanResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [timeline, setTimeline] = useState<TimelineStep[]>([]);
  const [target, setTarget] = useState<string>('');
  const [runStartedAt, setRunStartedAt] = useState<number | null>(null);

  const [history, setHistory] = useState<HistoryEntry[]>(loadHistory);
  const [summaries, setSummaries] = useState<Record<string, ScanSummary>>({});
  const [activeId, setActiveId] = useState<string | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [railOpen, setRailOpen] = useState(() => {
    try {
      return localStorage.getItem(RAIL_KEY) !== 'closed';
    } catch {
      return true;
    }
  });

  useEffect(() => {
    try {
      localStorage.setItem(RAIL_KEY, railOpen ? 'open' : 'closed');
    } catch {
      // A preference, not a requirement.
    }
  }, [railOpen]);

  // The drawer behaves like a dialog: Escape closes it and the page behind stops scrolling.
  useEffect(() => {
    if (!drawerOpen) return;
    const onKey = (event: KeyboardEvent) => event.key === 'Escape' && setDrawerOpen(false);
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    window.addEventListener('keydown', onKey);
    return () => {
      document.body.style.overflow = previous;
      window.removeEventListener('keydown', onKey);
    };
  }, [drawerOpen]);

  // Ctrl+B / Cmd+B toggles the sidebar, as in most apps with one.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key.toLowerCase() !== 'b' || !(event.ctrlKey || event.metaKey)) return;
      if (event.altKey || event.shiftKey) return;
      const typing = (event.target as HTMLElement | null)?.closest(
        'input, textarea, [contenteditable]',
      );
      if (typing) return; // leave the shortcut to text fields (bold, etc.)
      event.preventDefault();
      if (window.matchMedia('(min-width: 64rem)').matches) setRailOpen((open) => !open);
      else setDrawerOpen((open) => !open);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const resultsRef = useRef<HTMLElement>(null);
  // Only one scan is shown at a time; switching aborts polling of the previous one.
  const following = useRef<AbortController | null>(null);

  useEffect(() => saveHistory(history), [history]);

  // Bring the runner into view the moment it first appears. Scrolling when the scan
  // merely starts does nothing: the page is still short and the runner not rendered.
  const runnerVisible = phase === 'running' && timeline.length > 0;
  useEffect(() => {
    if (runnerVisible) resultsRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [runnerVisible]);

  // Refreshes can outlive the render that started them (a scan finishing minutes
  // later), so they read the current list through a ref rather than a stale closure.
  const historyRef = useRef(history);
  historyRef.current = history;

  const refreshHistory = useCallback(async () => {
    const requested = historyRef.current.map((e) => e.id);
    if (!isLiveApi || requested.length === 0) return;
    try {
      const rows = await fetchHistory(requested);
      const known = new Set(rows.map((row) => row.id));
      // Only forget ids this request asked about and the server no longer has
      // (expired after 7 days, or deleted elsewhere). An entry added while the
      // request was in flight was not asked about, so it must survive.
      const gone = new Set(requested.filter((id) => !known.has(id)));
      setSummaries((prev) => {
        const next = { ...prev };
        for (const id of gone) delete next[id];
        for (const row of rows) next[row.id] = row;
        return next;
      });
      if (gone.size) setHistory((prev) => prev.filter((e) => !gone.has(e.id)));
    } catch {
      // Leave the sidebar as it was; the next refresh will try again.
    }
  }, []);

  const historyKey = history.map((e) => e.id).join(',');
  useEffect(() => {
    void refreshHistory();
  }, [refreshHistory, historyKey, phase]);

  // Keep running entries current without the user reopening them.
  const anyRunning = Object.values(summaries).some(
    (s) => s.status === 'running' || s.status === 'queued',
  );
  useEffect(() => {
    if (!anyRunning) return;
    const timer = setInterval(() => void refreshHistory(), HISTORY_REFRESH_MS);
    return () => clearInterval(timer);
  }, [anyRunning, refreshHistory]);

  function resetView() {
    following.current?.abort();
    following.current = null;
    setProgress([]);
    setTimeline([]);
    setResult(null);
    setError(null);
  }

  /** Follow a scan to completion, whether just started or reopened from history. */
  async function follow(id: string, resumed: boolean) {
    const controller = new AbortController();
    following.current = controller;
    try {
      const scan = await followScan(
        id,
        (update: LiveUpdate) => {
          setProgress(update.progress);
          setTimeline(update.timeline);
          setTarget(update.targetUrl);
          if (update.status === 'running' || update.status === 'queued') {
            if (resumed && update.startedAt) setRunStartedAt(Date.parse(update.startedAt));
            setPhase('running');
          }
        },
        controller.signal,
      );
      setResult(scan);
      setPhase('done');
    } catch (cause) {
      if (controller.signal.aborted) return; // the user moved on to another scan
      setError(cause instanceof Error ? cause.message : 'Unknown failure.');
      setPhase('error');
    } finally {
      if (following.current === controller) following.current = null;
      void refreshHistory();
    }
  }

  async function handleScan(url: string) {
    resetView();
    setPhase('running');
    setTarget(url);
    setRunStartedAt(Date.now());

    if (!isLiveApi) {
      try {
        const scan = await runMockScan(url, options, (message) =>
          setProgress((prev) => [...prev, message]),
        );
        setResult(scan);
        setPhase('done');
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : 'Unknown failure.');
        setPhase('error');
      }
      return;
    }

    let started;
    try {
      started = await startScan(url, options);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unknown failure.');
      setPhase('error');
      return;
    }
    setActiveId(started.id);
    setHistory((prev) => [
      {
        id: started.id,
        ownerToken: started.ownerToken,
        targetUrl: url,
        createdAt: new Date().toISOString(),
      },
      ...prev.filter((e) => e.id !== started.id),
    ]);
    await follow(started.id, false);
  }

  function openFromHistory(id: string) {
    setDrawerOpen(false);
    if (id === activeId && following.current) return; // already watching it live
    resetView();
    setActiveId(id);
    setTarget(history.find((e) => e.id === id)?.targetUrl ?? '');
    void follow(id, true);
    resultsRef.current?.scrollIntoView({ behavior: 'smooth' });
  }

  function newTest() {
    resetView();
    setActiveId(null);
    setPhase('idle');
    setDrawerOpen(false);
    window.scrollTo({ top: 0, behavior: 'smooth' });
    document
      .querySelector<HTMLInputElement>('input[aria-label="Address of the app to test"]')
      ?.focus();
  }

  async function removeFromHistory(id: string) {
    const entry = history.find((e) => e.id === id);
    if (!entry) return;
    await deleteScan(id, entry.ownerToken);
    setHistory((prev) => prev.filter((e) => e.id !== id));
    setSummaries(({ [id]: _, ...rest }) => rest);
    if (id === activeId) newTest();
  }

  const sidebar = (mode: 'expanded' | 'collapsed' | 'drawer', onToggle: () => void) => (
    <HistorySidebar
      entries={history}
      summaries={summaries}
      activeId={activeId}
      onOpen={openFromHistory}
      onNewTest={newTest}
      onDelete={removeFromHistory}
      mode={mode}
      onToggle={onToggle}
    />
  );

  return (
    <div className="flex min-h-full">
      {isLiveApi ? (
        <>
          {/* Desktop: a rail that collapses to an icon strip rather than disappearing,
              so its toggle never moves. Width animates; each state renders at its own
              fixed width, so content never squashes mid-animation. */}
          <div
            className={`sticky top-0 hidden h-screen flex-none overflow-hidden transition-[width] duration-200 ease-out lg:block ${
              railOpen ? 'w-72' : 'w-14'
            }`}
          >
            <div className={`h-full ${railOpen ? 'w-72' : 'w-14'}`}>
              {sidebar(railOpen ? 'expanded' : 'collapsed', () => setRailOpen((open) => !open))}
            </div>
          </div>

          {/* Smaller screens: a drawer over the page. */}
          {drawerOpen ? (
            <div className="fixed inset-0 z-40 lg:hidden" role="dialog" aria-label="Test history">
              <button
                type="button"
                aria-label="Close history"
                className="absolute inset-0 bg-black/50"
                onClick={() => setDrawerOpen(false)}
              />
              <div className="relative h-full w-80 max-w-[85vw] animate-rise">
                {sidebar('drawer', () => setDrawerOpen(false))}
              </div>
            </div>
          ) : null}
        </>
      ) : null}

      <div className="relative flex min-w-0 flex-1 flex-col overflow-x-clip">
        <div
          className="glow pointer-events-none absolute -top-80 left-1/2 h-160 w-225 -translate-x-1/2 blur-2xl"
          aria-hidden="true"
        />

        <header className="relative mx-auto flex w-full max-w-270 items-center justify-between gap-3 px-4 py-4 sm:px-6 sm:py-5">
          <div className="flex items-center gap-2.5">
            {isLiveApi ? (
              <button
                type="button"
                onClick={() => setDrawerOpen(true)}
                aria-label="Show test history"
                className="flex items-center gap-1.5 rounded-[9px] border border-line bg-surface px-2.5 py-1.5 text-[12.5px] text-muted transition hover:text-ink lg:hidden"
              >
                <svg
                  className="size-4"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth={2}
                  strokeLinecap="round"
                  aria-hidden="true"
                >
                  <path d="M4 6h16M4 12h16M4 18h10" />
                </svg>
                <span className="hidden sm:inline">History</span>
                {history.length ? (
                  <span className="rounded-full bg-raised px-1.5 font-mono text-[11px]">
                    {history.length}
                  </span>
                ) : null}
              </button>
            ) : null}
            <span
              className="size-5.5 rounded-[7px] bg-linear-[140deg] from-accent to-[#ff9c6b] ring-1 ring-accent/30"
              aria-hidden="true"
            />
            <span className="font-semibold tracking-tight">AI App Tester</span>
          </div>
          <ThemeToggle />
        </header>

        <main className="relative mx-auto w-full max-w-205 flex-1 px-4 pt-6 pb-14 sm:px-6 sm:pt-9 sm:pb-16">
          <section className="mb-8 text-center">
            {/* The lineage, for anyone who knows the names; the headline is for everyone else. */}
            <span className="mb-4 inline-flex items-center gap-1.5 rounded-full border border-line-strong bg-surface px-3 py-1 text-[11.5px] font-medium tracking-[0.06em] text-muted uppercase">
              Cypress
              <span className="text-accent" aria-hidden="true">
                ×
              </span>
              <span className="sr-only">plus</span>
              Playwright
              <span className="text-accent" aria-hidden="true">
                ×
              </span>
              <span className="sr-only">plus</span>
              Claude
            </span>

            <h1 className="text-[clamp(2rem,5.2vw,3.1rem)] leading-[1.08] font-semibold tracking-[-0.035em] text-balance">
              Tests your app like a person.
              <br />
              <span className="text-accent">Reports like an engineer.</span>
            </h1>

            <p className="mx-auto mt-4 max-w-[56ch] text-[15.5px] text-pretty text-muted">
              Cypress&rsquo;s time-travel replay and Playwright&rsquo;s real browser, with Claude
              deciding what to try. Paste an address, watch it explore, and get back what broke and
              what could be better, every finding backed by evidence.
            </p>
          </section>

          <ScanForm
            options={options}
            onOptionsChange={setOptions}
            onSubmit={handleScan}
            busy={phase === 'running'}
            busySince={runStartedAt}
          />
        </main>

        {/* Wider than the form: the runner needs room for a log beside a real viewport. */}
        <section
          ref={resultsRef}
          className="@container relative mx-auto -mt-8 w-full max-w-300 scroll-mt-4 px-4 pb-16 sm:px-6"
        >
          <FindingsPanel
            key={activeId ?? 'none'}
            phase={phase}
            progress={progress}
            result={result}
            error={error}
            timeline={timeline}
            targetUrl={target}
            runStartedAt={runStartedAt}
          />
        </section>

        <footer className="relative flex flex-wrap items-center justify-center gap-2.5 px-6 pt-4 pb-8 text-center text-[12.5px] text-faint">
          {isLiveApi ? (
            <span>
              Evidence from a real Chromium browser. Judgement from Claude. Every finding cites what
              the browser recorded.
            </span>
          ) : (
            <>
              <span className="rounded-full border border-medium/40 px-2.5 py-0.5 font-medium text-medium">
                Placeholder results
              </span>
              <span>
                This build is not connected to the scan API. Findings shown are fixed sample data
                from
                <code className="ml-1 font-mono text-[0.86em] text-muted">src/lib/mockScan.ts</code>
                .
              </span>
            </>
          )}
        </footer>
      </div>
    </div>
  );
}
