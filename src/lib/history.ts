/**
 * The list of scans this browser started, with the token that proves ownership.
 *
 * There are no accounts, so history is per browser by design: the server never
 * lists everyone's scans, and only the browser holding a scan's token can delete
 * it. Storage can be unavailable (private windows, blocked site data), so every
 * access is guarded and the app works without it.
 */

export interface HistoryEntry {
  id: string;
  ownerToken: string;
  targetUrl: string;
  createdAt: string;
}

const KEY = 'ai-app-tester:history';
/** Matches the API's cap on ids per history request. */
export const MAX_HISTORY = 30;

function isEntry(value: unknown): value is HistoryEntry {
  const entry = value as HistoryEntry;
  return (
    typeof entry?.id === 'string' &&
    /^[0-9a-f]{32}$/.test(entry.id) &&
    typeof entry.ownerToken === 'string' &&
    typeof entry.targetUrl === 'string'
  );
}

export function loadHistory(): HistoryEntry[] {
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(KEY) ?? '[]');
    return Array.isArray(parsed) ? parsed.filter(isEntry).slice(0, MAX_HISTORY) : [];
  } catch {
    return [];
  }
}

export function saveHistory(entries: HistoryEntry[]): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(entries.slice(0, MAX_HISTORY)));
  } catch {
    // History is a convenience; a scan still works without it.
  }
}
