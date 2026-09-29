import { useEffect, useState } from 'react';

/** 83_000 -> "1:23", 3_723_000 -> "1:02:03". Stopwatch style, so digits never jump around. */
export function formatDuration(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = String(total % 60).padStart(2, '0');
  return hours
    ? `${hours}:${String(minutes).padStart(2, '0')}:${seconds}`
    : `${minutes}:${seconds}`;
}

/** Milliseconds since `startMs`, re-rendering four times a second while `running`. */
export function useElapsed(startMs: number | null | undefined, running: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!running) return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(timer);
  }, [running]);
  return startMs ? Math.max(0, now - startMs) : 0;
}

/** Server-reported run length, when both ends are known. */
export function durationBetween(start?: string | null, end?: string | null): number | null {
  const from = start ? Date.parse(start) : NaN;
  const to = end ? Date.parse(end) : NaN;
  return Number.isFinite(from) && Number.isFinite(to) ? Math.max(0, to - from) : null;
}
