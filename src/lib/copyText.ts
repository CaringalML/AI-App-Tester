import type { Finding } from './types';

/*
 * Short plain-text pieces of a finding for the copy buttons: the bug itself,
 * the steps, or the fix. Evidence is written for the scanner's own grounding,
 * so the copies drop its internal references (act-1, obs-6) and the raw
 * evidence log that follows the prose.
 */

/** A page path from the finding, made absolute against the tested site. */
export function pageUrl(location: string, targetUrl?: string): string {
  const first = location.split(/[,\s]/)[0];
  if (!targetUrl || !first) return location;
  try {
    return new URL(first, targetUrl).href;
  } catch {
    return location;
  }
}

const RECORDED = /\n\s*Recorded evidence:\n?/;
/** A bracketed list of the scanner's ids, like "(act-1)" or "(e15, e17, …, e61)". */
const ID_LIST = /\s*\((?:\s*(?:e\d+|(?:act|obs)-\d+|…|\.{3})\s*,?)+\s*\)/g;

/** The evidence prose without the scanner's reference ids or its recorded log. */
export function readableEvidence(evidence: string): string {
  return evidence.split(RECORDED)[0].replace(ID_LIST, '').trim();
}

/**
 * The browser log lines a finding cites, for display: "clicked "Login" · URL
 * unchanged" rather than "act-3 [action] clicked e15 "Login" | URL unchanged".
 */
export function recordedEvidence(evidence: string): string[] {
  const log = evidence.split(RECORDED)[1];
  if (!log) return [];
  return log
    .split('\n')
    .map((line) =>
      line
        .replace(/^\s*-\s*/, '')
        .replace(/\b(?:act|obs)-\d+ \[[a-z-]+\]\s*/g, '')
        .replace(/\be\d+ (?=")/g, '')
        .replace(/ \| /g, ' · ')
        .trim(),
    )
    .filter(Boolean);
}

export function bugText(finding: Finding, targetUrl?: string): string {
  const where = pageUrl(finding.location, targetUrl);
  return [
    finding.title,
    `Page: ${where}${finding.selector ? ` (element: ${finding.selector})` : ''}`,
    '',
    readableEvidence(finding.evidence),
  ].join('\n');
}

export function stepsText(finding: Finding): string {
  return finding.steps.map((step, index) => `${index + 1}. ${step}`).join('\n');
}
