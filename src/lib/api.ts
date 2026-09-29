/**
 * Client for the scan API in backend-fastapi.
 *
 * A scan runs for a minute or two, far longer than one HTTP request should
 * stay open through Cloudflare and a load balancer, so the flow is: start the
 * scan, get an id back immediately, then poll its record and stream the
 * progress log into the UI until it finishes.
 */
import type { ScanOptions, ScanResult } from './types';

const API_URL = import.meta.env.VITE_API_URL?.replace(/\/+$/, '');

/** True when the build was pointed at a real backend. */
export const isLiveApi = Boolean(API_URL);

const POLL_MS = 1500;
const GIVE_UP_MS = 8 * 60 * 1000;

interface ApiScan extends Omit<ScanResult, 'pagesVisited'> {
  status: 'queued' | 'running' | 'done' | 'error';
  error?: string | null;
  visitedUrls: string[];
  progress: { at: string; message: string; kind: string }[];
}

export class ScanError extends Error {}

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

export async function runLiveScan(
  targetUrl: string,
  options: ScanOptions,
  onProgress: (messages: string[]) => void,
): Promise<ScanResult> {
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
  const { id } = (await started.json()) as { id: string };

  const giveUpAt = Date.now() + GIVE_UP_MS;
  while (Date.now() < giveUpAt) {
    await wait(POLL_MS);
    let response: Response;
    try {
      response = await fetch(`${API_URL}/scans/${id}`, { cache: 'no-store' });
    } catch {
      continue; // a dropped poll is not a failed scan; try again next tick
    }
    if (!response.ok) throw new ScanError(await readError(response));

    const scan = (await response.json()) as ApiScan;
    onProgress(scan.progress.map((event) => event.message));

    if (scan.status === 'error' && scan.findings.length === 0) {
      throw new ScanError(scan.error ?? 'The scan failed.');
    }
    if (scan.status === 'done' || scan.status === 'error') {
      return {
        ...scan,
        pagesVisited: scan.visitedUrls.length,
        startedAt: scan.startedAt ?? '',
        finishedAt: scan.finishedAt ?? '',
        notes: scan.error ? [scan.error, ...(scan.notes ?? [])] : scan.notes,
      };
    }
  }
  throw new ScanError('The scan is taking longer than expected. Try again with fewer pages.');
}

export function reportUrl(scanId: string): string {
  return `${API_URL}/scans/${scanId}/report.md`;
}
