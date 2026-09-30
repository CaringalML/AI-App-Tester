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

/**
 * When a test ran, in the viewer's own time zone: "Today, 2:14 pm",
 * "Yesterday, 9:03 am", "Mon 28 Sep, 4:40 pm", "26 Aug 2025, 12:55 pm".
 */
export function formatWhen(iso: string, now: number = Date.now()): string {
  const then = new Date(iso);
  if (!Number.isFinite(then.getTime())) return '';
  const time = then.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
  const today = new Date(now);
  const dayDiff = Math.round(
    (new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime() -
      new Date(then.getFullYear(), then.getMonth(), then.getDate()).getTime()) /
      86_400_000,
  );
  if (dayDiff === 0) return `Today, ${time}`;
  if (dayDiff === 1) return `Yesterday, ${time}`;
  // An older year takes the weekday's place, so the line stays short.
  const date = then.toLocaleDateString(
    undefined,
    then.getFullYear() === today.getFullYear()
      ? { weekday: 'short', day: 'numeric', month: 'short' }
      : { day: 'numeric', month: 'short', year: 'numeric' },
  );
  return `${date}, ${time}`;
}

/** The full date and time, for tooltips. */
export function formatFullDate(iso: string): string {
  const then = new Date(iso);
  return Number.isFinite(then.getTime())
    ? then.toLocaleString(undefined, { dateStyle: 'full', timeStyle: 'short' })
    : '';
}

/** "just now", "5 min ago", "3 h ago", "2 days ago", then a date. */
export function formatAgo(iso: string, now: number = Date.now()): string {
  const then = Date.parse(iso);
  if (!Number.isFinite(then)) return '';
  const seconds = Math.max(0, (now - then) / 1000);
  if (seconds < 45) return 'just now';
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 86_400) return `${Math.round(seconds / 3600)} h ago`;
  if (seconds < 7 * 86_400) {
    const days = Math.round(seconds / 86_400);
    return `${days} day${days === 1 ? '' : 's'} ago`;
  }
  return new Date(then).toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
}
