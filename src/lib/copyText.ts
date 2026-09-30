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

/** The evidence prose without the scanner's reference ids or its recorded log. */
export function readableEvidence(evidence: string): string {
  return evidence
    .split(/\n\s*Recorded evidence:/)[0]
    .replace(/\s*\((?:act|obs)-\d+(?:\s*,\s*(?:act|obs)-\d+)*\)/g, '')
    .trim();
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
