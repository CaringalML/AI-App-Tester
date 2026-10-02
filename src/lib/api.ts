/**
 * Client for the scan API in backend-fastapi.
 *
 * A scan runs for a minute or two, far longer than one HTTP request should
 * stay open through Cloudflare and a load balancer, so the flow is: start the
 * scan, get an id back immediately, then poll its record and stream the
 * progress log into the UI until it finishes.
 */
import type {
  ScanOptions,
  ScanResult,
  ScanStage,
  ScanStatus,
  ScanSummary,
  TimelineStep,
} from './types';

const API_URL = import.meta.env.VITE_API_URL?.replace(/\/+$/, '');

/** True when the build was pointed at a real backend. */
export const isLiveApi = Boolean(API_URL);

const POLL_MS = 1500;
// Past the longest scan (a thorough one may run 30 minutes) with room to spare.
const GIVE_UP_MS = 40 * 60 * 1000;
/** Consecutive 5xx polls (about 12 seconds' worth) before a scan is reported as lost. */
const MAX_SERVER_ERRORS = 8;

interface ApiScan extends Omit<ScanResult, 'pagesVisited'> {
  status: ScanStatus;
  stage?: ScanStage;
  completion?: number;
  error?: string | null;
  visitedUrls: string[];
  progress: { at: string; message: string; kind: string }[];
}

export class ScanError extends Error {}

/** What the UI can show while a scan is still running. */
export interface LiveUpdate {
  status: ScanStatus;
  /** Server start time, used to resume the live timer when reopening a running scan. */
  startedAt: string | null;
  targetUrl: string;
  progress: string[];
  timeline: TimelineStep[];
  stage: ScanStage;
  /** 0 to 1, from the server; only ever increases. */
  completion: number;
}

async function readError(response: Response): Promise<string> {
  try {
    const body = await response.json();
    if (typeof body.detail === 'string') return body.detail;
    if (Array.isArray(body.detail)) return 'That request was not valid.';
  } catch {
    // Not JSON, fall through to a generic message.
  }
  return `The tester responded with HTTP ${response.status}.`;
}

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

function toResult(scan: ApiScan): ScanResult {
  return {
    ...scan,
    pagesVisited: scan.visitedUrls.length,
    startedAt: scan.startedAt ?? '',
    finishedAt: scan.finishedAt ?? '',
    notes: scan.error ? [scan.error, ...(scan.notes ?? [])] : scan.notes,
  };
}

export interface StartedScan {
  id: string;
  /** Proof of ownership for deleting the scan later. Kept in this browser only. */
  ownerToken: string;
}

export async function startScan(targetUrl: string, options: ScanOptions): Promise<StartedScan> {
  let started: Response;
  try {
    started = await fetch(`${API_URL}/scans`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: targetUrl, options }),
    });
  } catch {
    throw new ScanError('Could not reach the tester. Check your connection and try again.');
  }
  if (!started.ok) throw new ScanError(await readError(started));
  return (await started.json()) as StartedScan;
}

/**
 * Poll a scan until it finishes, reporting progress on the way. Used both for a
 * scan just started and for reopening one from history, so a run that is still
 * going resumes its live view. Resolves immediately for a finished scan.
 */
export async function followScan(
  id: string,
  onUpdate: (update: LiveUpdate) => void,
  signal?: AbortSignal,
): Promise<ScanResult> {
  const giveUpAt = Date.now() + GIVE_UP_MS;
  let first = true;
  let serverErrors = 0;
  while (Date.now() < giveUpAt) {
    if (!first) await wait(POLL_MS);
    first = false;
    if (signal?.aborted) throw new DOMException('Stopped following this scan', 'AbortError');

    let response: Response;
    try {
      response = await fetch(`${API_URL}/scans/${id}`, { cache: 'no-store', signal });
    } catch (cause) {
      if (signal?.aborted) throw cause;
      continue; // a dropped poll is not a failed scan; try again next tick
    }
    if (!response.ok) {
      // Cloudflare or the load balancer can fail a single poll (502, 503, 504)
      // while the scan carries on, so a few in a row are retried before giving up.
      const transient = response.status >= 500 || response.status === 429;
      if (transient && ++serverErrors < MAX_SERVER_ERRORS) continue;
      throw new ScanError(await readError(response));
    }
    serverErrors = 0;

    const scan = (await response.json()) as ApiScan;
    if (signal?.aborted) throw new DOMException('Stopped following this scan', 'AbortError');
    onUpdate({
      status: scan.status,
      startedAt: scan.startedAt ?? null,
      targetUrl: scan.targetUrl,
      progress: scan.progress.map((event) => event.message),
      timeline: scan.timeline ?? [],
      stage: scan.stage ?? 'queued',
      completion: scan.completion ?? 0,
    });

    if (scan.status === 'error' && scan.findings.length === 0) {
      throw new ScanError(scan.error ?? 'The scan failed.');
    }
    if (scan.status === 'done' || scan.status === 'error') return toResult(scan);
  }
  throw new ScanError('The scan is taking longer than expected. Try again with fewer pages.');
}

export async function fetchHistory(ids: string[]): Promise<ScanSummary[]> {
  if (!ids.length) return [];
  const response = await fetch(`${API_URL}/scans?ids=${ids.join(',')}`, { cache: 'no-store' });
  if (!response.ok) throw new ScanError(await readError(response));
  return (await response.json()) as ScanSummary[];
}

export async function deleteScan(id: string, ownerToken: string): Promise<void> {
  let response: Response;
  try {
    response = await fetch(`${API_URL}/scans/${id}`, {
      method: 'DELETE',
      headers: { 'X-Owner-Token': ownerToken },
    });
  } catch {
    throw new ScanError('Could not reach the tester to delete this scan.');
  }
  // Already gone (expired or deleted elsewhere) is the outcome the user asked for.
  if (!response.ok && response.status !== 404) throw new ScanError(await readError(response));
}

/** Stop a running scan early; the server keeps and reports what it found so far. */
export async function stopScan(id: string, ownerToken: string): Promise<void> {
  let response: Response;
  try {
    response = await fetch(`${API_URL}/scans/${id}/stop`, {
      method: 'POST',
      headers: { 'X-Owner-Token': ownerToken },
    });
  } catch {
    throw new ScanError('Could not reach the tester to stop this scan.');
  }
  // 409: it finished on its own in the meantime, which is what the user wanted anyway.
  if (!response.ok && response.status !== 409) throw new ScanError(await readError(response));
}

export function testsUrl(scanId: string): string {
  return `${API_URL}/scans/${scanId}/tests.spec.ts`;
}

export function reportUrl(scanId: string): string {
  return `${API_URL}/scans/${scanId}/report.md`;
}
