/**
 * The shape of everything the scanner produces. Mirrors the API's Pydantic
 * models in backend-fastapi/app/models.py, which serialize to these names.
 *
 * Every finding carries evidence, reproduction steps and a suggested fix, and
 * declares how confident the tester is that it is real. `source` says whether
 * the browser recorded it directly or the AI tester reported it, and
 * `evidenceIds` point at what the browser logged to back it up.
 */

export type Category = 'bug' | 'improvement';

export type Severity = 'critical' | 'high' | 'medium' | 'low';

/** How sure the tester is that this is a genuine problem, not a false positive. */
export type Confidence = 'high' | 'medium' | 'low';

/** automated: recorded directly by the browser. agent: reported by Claude, with cited evidence. */
export type Source = 'automated' | 'agent';

export type Kind =
  | 'crash'
  | 'console'
  | 'network'
  | 'broken-link'
  | 'functional'
  | 'accessibility'
  | 'performance'
  | 'seo'
  | 'ux'
  | 'content';

export interface Finding {
  id: string;
  title: string;
  category: Category;
  severity: Severity;
  confidence: Confidence;
  kind?: Kind;
  source?: Source;
  /** Page or route where the finding was observed. */
  location: string;
  /** Element the finding points at, when it is element specific. */
  selector?: string | null;
  /** What the tester actually observed. Raw enough to be checkable. */
  evidence: string;
  /** Ordered steps another person can follow to see it for themselves. */
  steps: string[];
  /** Plain language suggestion a developer can act on. */
  suggestion: string;
  evidenceIds?: string[];
  screenshotUrl?: string | null;
}

export interface SuppressedFinding {
  finding: Finding;
  reason: string;
}

export type StepKind =
  | 'stage'
  | 'visit'
  | 'click'
  | 'type'
  | 'select'
  | 'press'
  | 'navigate'
  | 'back'
  | 'look'
  | 'read'
  | 'finding';

/** Element position as fractions of the viewport, measured just before the action. */
export interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** One row of the Cypress-style command log, with the page as it looked at that moment. */
export interface TimelineStep {
  index: number;
  at: string;
  kind: StepKind;
  label: string;
  /** Claude's one-line reason for the action, written for whoever is watching. */
  why?: string | null;
  url?: string | null;
  status: 'ok' | 'failed' | 'info' | 'warning' | 'finding';
  actionId?: string | null;
  findingId?: string | null;
  signals: number;
  box?: Box | null;
  /** The page after the step. */
  screenshotUrl?: string | null;
  /** The page just before an element action, which is where `box` applies. */
  beforeUrl?: string | null;
}

export interface Usage {
  requests: number;
  inputTokens: number;
  outputTokens: number;
  estimatedCostUsd?: number | null;
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
  /** Present for live scans; used to link the Markdown report. */
  id?: string;
  targetUrl: string;
  startedAt: string;
  finishedAt: string;
  pagesVisited: number;
  findings: Finding[];
  timeline?: TimelineStep[];
  summary?: string | null;
  suppressed?: SuppressedFinding[];
  notes?: string[];
  agentSteps?: number;
  model?: string;
  usage?: Usage;
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
