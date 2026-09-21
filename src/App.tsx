import { useState } from 'react';
import { ScanForm } from './components/ScanForm';
import { FindingsPanel } from './components/FindingsPanel';
import { ThemeToggle } from './components/ThemeToggle';
import { DEFAULT_OPTIONS } from './lib/types';
import type { ScanOptions, ScanPhase, ScanResult } from './lib/types';
import { runMockScan } from './lib/mockScan';

export default function App() {
  const [options, setOptions] = useState<ScanOptions>(DEFAULT_OPTIONS);
  const [phase, setPhase] = useState<ScanPhase>('idle');
  const [progress, setProgress] = useState<string[]>([]);
  const [result, setResult] = useState<ScanResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleScan(url: string) {
    setPhase('running');
    setProgress([]);
    setResult(null);
    setError(null);

    try {
      // TODO: replace with POST /api/scan once the engine lands.
      const scan = await runMockScan(url, options, (message) =>
        setProgress((prev) => [...prev, message]),
      );
      setResult(scan);
      setPhase('done');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unknown failure.');
      setPhase('error');
    }
  }

  return (
    <div className="relative flex min-h-full flex-col overflow-x-hidden">
      <div
        className="glow pointer-events-none absolute -top-80 left-1/2 h-160 w-225 -translate-x-1/2 blur-2xl"
        aria-hidden="true"
      />

      <header className="relative mx-auto flex w-full max-w-270 items-center justify-between gap-4 px-6 py-5">
        <div className="flex items-center gap-2.5">
          <span
            className="size-5.5 rounded-[7px] bg-linear-[140deg] from-accent to-[#ff9c6b] ring-1 ring-accent/30"
            aria-hidden="true"
          />
          <span className="font-semibold tracking-tight">AI App Tester</span>
        </div>
        <ThemeToggle />
      </header>

      <main className="relative mx-auto w-full max-w-205 flex-1 px-6 pt-9 pb-16">
        <section className="mb-8 text-center">
          <span className="mb-4 inline-block rounded-full border border-line-strong bg-surface px-2.5 py-1 text-[11.5px] font-medium tracking-[0.06em] text-muted uppercase">
            Prototype
          </span>

          <h1 className="text-[clamp(2rem,5.2vw,3.1rem)] leading-[1.08] font-semibold tracking-[-0.035em]">
            Point it at your app.
            <br />
            It finds what is broken.
          </h1>

          <p className="mx-auto mt-4 max-w-[54ch] text-[15.5px] text-muted">
            No test cases to write, no scripts to maintain. Give it an address and it explores the
            app the way a person would, then reports what failed and what could be better in plain
            language.
          </p>
        </section>

        <ScanForm
          options={options}
          onOptionsChange={setOptions}
          onSubmit={handleScan}
          busy={phase === 'running'}
        />

        <FindingsPanel phase={phase} progress={progress} result={result} error={error} />
      </main>

      <footer className="relative flex flex-wrap items-center justify-center gap-2.5 px-6 pt-4 pb-8 text-center text-[12.5px] text-faint">
        <span className="rounded-full border border-medium/40 px-2.5 py-0.5 font-medium text-medium">
          Placeholder results
        </span>
        <span>
          The scan engine is not wired up yet. Findings shown are fixed sample data from
          <code className="ml-1 font-mono text-[0.86em] text-muted">src/lib/mockScan.ts</code>.
        </span>
      </footer>
    </div>
  );
}
