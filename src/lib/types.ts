/**
 * The shape of everything the scanner produces.
 *
 * This is deliberately opinionated: the brief asks what makes a bug report
 * something a developer can act on rather than noise. The answer encoded here
 * is that every finding must carry evidence, reproduction steps and a
 * suggested fix, and must declare how confident the model is that it is real.
 */

export type Category = 'bug' | 'improvement';

export type Severity = 'critical' | 'high' | 'medium' | 'low';

/** How sure the model is that this is a genuine problem, not a false positive. */
export type Confidence = 'high' | 'medium' | 'low';

export interface Finding {
  id: string;
  title: string;
  category: Category;
  severity: Severity;
  confidence: Confidence;
  /** Page or route where the finding was observed. */
  location: string;
  /** Element the finding points at, when it is element specific. */
  selector?: string;
  /** What the tester actually observed. Raw enough to be checkable. */
  evidence: string;
  /** Ordered steps another person can follow to see it for themselves. */
  steps: string[];
  /** Plain language suggestion a developer can act on. */
  suggestion: string;
}

export type ScanPhase = 'idle' | 'running' | 'done' | 'error';

export interface ScanOptions {
  findBugs: boolean;
  findImprovements: boolean;
  checkAccessibility: boolean;
  /** Upper bound on pages explored, so a scan cannot run away during a demo. */
  maxPages: number;
}

export interface ScanResult {
  targetUrl: string;
  startedAt: string;
  finishedAt: string;
  pagesVisited: number;
  findings: Finding[];
}

export const DEFAULT_OPTIONS: ScanOptions = {
  findBugs: true,
  findImprovements: true,
  checkAccessibility: true,
  maxPages: 5,
};

export const SEVERITY_ORDER: Record<Severity, number> = {
  critical: 0,
  high: 1,
  medium: 2,
  low: 3,
};
