import { useCallback, useEffect, useRef, useState } from 'react';
import { ScanForm } from './components/ScanForm';
import { FindingsPanel } from './components/FindingsPanel';
import { HistorySidebar } from './components/HistorySidebar';
import { ThemeToggle } from './components/ThemeToggle';
import { TopProgress } from './components/TopProgress';
import { FinishToast, type FinishNotice } from './components/FinishToast';
import { DEFAULT_OPTIONS } from './lib/types';
import type {
  ScanOptions,
  ScanPhase,
  ScanResult,
  ScanDepth,
  ScanStage,
  ScanSummary,
  TimelineStep,
} from './lib/types';
import { durationBetween, formatDuration } from './lib/time';
import { runMockScan } from './lib/mockScan';
import {
  deleteScan,
  fetchHistory,
  followScan,
  isLiveApi,
  stopScan,
  startScan,
  type LiveUpdate,
} from './lib/api';
import { loadHistory, saveHistory, type HistoryEntry } from './lib/history';

const HISTORY_REFRESH_MS = 8000;

const STAGE_LABEL: Record<ScanStage, string> = {
  queued: 'Starting',
  checking: 'Checking the address',
  loading: 'Opening the page',
  crawling: 'Checking pages and links',
  exploring: 'Claude is exploring',
  reviewing: 'Reviewing findings',
  done: 'Done',
  error: 'Stopped',
};

/** "https://www.example.com/login" -> "example.com/login", for a compact heading. */
function displayUrl(url: string): string {
  try {
    const parsed = new URL(url);
    const path = parsed.pathname === '/' ? '' : parsed.pathname;
    return parsed.host.replace(/^www\./, '') + path;
  } catch {
    return url;
  }
}
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
  const [stage, setStage] = useState<ScanStage>('queued');
  const [completion, setCompletion] = useState(0);
  // Quick or thorough, from the server for the scan on screen.
  const [runDepth, setRunDepth] = useState<ScanDepth>('quick');
  const [notice, setNotice] = useState<FinishNotice | null>(null);
  const closeNotice = useCallback(() => setNotice(null), []);

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

  // Only one scan is shown at a time; switching aborts polling of the previous one.
  const following = useRef<AbortController | null>(null);

  useEffect(() => saveHistory(history), [history]);

  // While a test runs or its results are open, the landing content (tagline and URL
  // form) steps aside so the run has the screen. New test brings it back.
  const focused = phase === 'running' || phase === 'done';
  useEffect(() => {
    if (focused) window.scrollTo({ top: 0, behavior: 'smooth' });
  }, [focused]);

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
    setStage('queued');
    setCompletion(0);
    setRunDepth('quick');
    setNotice(null);
  }

  /** Follow a scan to completion, whether just started or reopened from history. */
  async function follow(id: string, resumed: boolean) {
    const controller = new AbortController();
    following.current = controller;
    let watchedLive = false;
    try {
      const scan = await followScan(
        id,
        (update: LiveUpdate) => {
          setProgress(update.progress);
          setTimeline(update.timeline);
          setTarget(update.targetUrl);
          setStage(update.stage);
          setCompletion((previous) => Math.max(previous, update.completion));
          setRunDepth(update.depth);
          if (update.status === 'running' || update.status === 'queued') {
            watchedLive = true;
            if (resumed && update.startedAt) setRunStartedAt(Date.parse(update.startedAt));
            setPhase('running');
          }
        },
        controller.signal,
      );
      setResult(scan);
      setPhase('done');
      if (watchedLive) {
        const bugs = scan.findings.filter((f) => f.category === 'bug').length;
        const took = durationBetween(scan.startedAt, scan.finishedAt);
        setNotice({
          ok: true,
          title: `Test finished${took !== null ? ` in ${formatDuration(took)}` : ''}`,
          detail: `${displayUrl(scan.targetUrl)}: ${bugs} broken, ${scan.findings.length - bugs} to improve.`,
        });
      }
    } catch (cause) {
      if (controller.signal.aborted) return; // the user moved on to another scan
      if (watchedLive) {
        setNotice({
          ok: false,
          title: 'The test stopped',
          detail: cause instanceof Error ? cause.message : 'Something went wrong.',
        });
      }
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
    setRunDepth(options.depth);

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
  }

  function newTest() {
    resetView();
    setActiveId(null);
    setPhase('idle');
    setDrawerOpen(false);
    window.scrollTo({ top: 0, behavior: 'smooth' });
    // The form is inert while collapsed; focus it after the render that restores it.
    setTimeout(() => {
      document
        .querySelector<HTMLInputElement>('input[aria-label="Address of the app to test"]')
        ?.focus({ preventScroll: true });
    }, 60);
  }

  // Stopping keeps what was found: the scan finishes as "done" and polling shows it.
  const [stopping, setStopping] = useState(false);
  const ownerToken = history.find((e) => e.id === activeId)?.ownerToken;
  useEffect(() => {
    if (phase !== 'running') setStopping(false);
  }, [phase]);

  async function stopActive() {
    if (!activeId || !ownerToken) return;
    setStopping(true);
    try {
      await stopScan(activeId, ownerToken);
    } catch (cause) {
      setStopping(false);
      setNotice({
        ok: false,
        title: 'Could not stop the test',
        detail: cause instanceof Error ? cause.message : 'Something went wrong.',
      });
    }
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

  // The tab title carries the status too, so a finished test shows up in another tab.
  const defaultTitle = useRef(document.title);
  useEffect(() => {
    const site = displayUrl(target);
    if (phase === 'running') {
      document.title = `(${Math.round(completion * 100)}%) Testing ${site}`;
    } else if (phase === 'done' && result) {
      document.title = `\u2713 Done: ${site}`;
    } else if (phase === 'error') {
      document.title = `\u2715 Stopped: ${site}`;
    } else {
      document.title = defaultTitle.current;
    }
  }, [phase, completion, target, result]);

  return (
    <div className="flex min-h-full">
      <TopProgress phase={phase} completion={completion} />
      <FinishToast notice={notice} onClose={closeNotice} />
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
            <img src="/images/logo-64.png" alt="" width={28} height={28} className="size-7" />
            <span className="font-semibold tracking-tight">AI App Tester</span>
          </div>
          <ThemeToggle />
        </header>

        {/* Collapses by animating grid rows from 1fr to 0fr: a smooth height change
            without measuring anything. Inert while hidden, so Tab cannot land in it. */}
        <div
          className={`grid transition-[grid-template-rows,opacity] duration-300 ease-out ${
            focused ? 'grid-rows-[0fr] opacity-0' : 'grid-rows-[1fr] opacity-100'
          }`}
          {...(focused ? { inert: '', 'aria-hidden': true } : {})}
        >
          <div className="min-h-0 overflow-hidden">
            <main className="relative mx-auto w-full max-w-205 px-4 pt-6 pb-14 sm:px-6 sm:pt-9 sm:pb-16">
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
                  Cypress&rsquo;s time-travel replay and Playwright&rsquo;s real browser, with
                  Claude deciding what to try. Watch it explore, see exactly what it saw, and keep
                  the bugs it reproduces as Playwright tests.
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
          </div>
        </div>

        {/* Wider than the form: the runner needs room for a log beside a real viewport. */}
        <section
          className={`@container relative mx-auto w-full max-w-300 flex-1 scroll-mt-4 px-4 pb-16 transition-[margin] duration-300 sm:px-6 ${
            focused ? 'mt-1' : '-mt-8'
          }`}
        >
          {focused ? (
            <div className="mb-4 flex animate-rise items-center justify-between gap-3">
              <p className="flex min-w-0 items-baseline gap-2">
                <span className="flex-none font-mono text-[11px] tracking-[0.08em] text-faint uppercase">
                  {phase === 'running' ? 'Testing' : 'Results for'}
                </span>
                <span className="truncate text-[14px] text-ink" title={target}>
                  {displayUrl(target)}
                </span>
                <span
                  className={`flex-none self-center rounded-full border px-2 py-px text-[11px] font-medium ${
                    runDepth === 'thorough'
                      ? 'border-improve/40 text-improve'
                      : 'border-line-strong text-muted'
                  }`}
                  title={
                    runDepth === 'thorough'
                      ? 'Thorough scan: up to 30 minutes and 100 actions'
                      : 'Quick scan: about 2 minutes, up to 30 actions'
                  }
                >
                  {runDepth === 'thorough' ? 'Thorough' : 'Quick'}
                </span>
                {phase === 'running' ? (
                  <span className="hidden flex-none text-[12.5px] text-faint sm:inline">
                    {STAGE_LABEL[stage]} &middot;{' '}
                    <span className="font-mono text-accent tabular-nums">
                      {Math.round(completion * 100)}%
                    </span>
                  </span>
                ) : null}
              </p>
              <div className="flex flex-none items-center gap-2">
                {phase === 'running' && isLiveApi && ownerToken ? (
                  <button
                    type="button"
                    onClick={stopActive}
                    disabled={stopping}
                    title="End the test now and keep what it has found so far"
                    className="flex items-center gap-1.5 rounded-lg border border-critical/40 px-2.5 py-1.5 text-[12.5px] text-critical transition hover:bg-critical/10 disabled:opacity-60"
                  >
                    <span className="size-2.5 rounded-[2px] bg-current" aria-hidden="true" />
                    {stopping ? 'Stopping…' : 'Stop'}
                  </button>
                ) : null}
                {/* Desktop has New test in the sidebar; smaller screens and the
                  placeholder build (no sidebar) need a way back here. */}
                <button
                  type="button"
                  onClick={newTest}
                  className={`flex flex-none items-center gap-1 rounded-lg border border-line-strong px-2.5 py-1.5 text-[12.5px] text-muted transition hover:border-accent/40 hover:text-ink ${
                    isLiveApi ? 'lg:hidden' : ''
                  }`}
                >
                  <svg
                    className="size-3.5"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth={2}
                    strokeLinecap="round"
                    aria-hidden="true"
                  >
                    <path d="M12 5v14M5 12h14" />
                  </svg>
                  New test
                </button>
              </div>
            </div>
          ) : null}
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
              Evidence from a real Chromium browser. Judgement from Claude. Findings without
              evidence are marked, never hidden.
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
