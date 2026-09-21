/**
 * PLACEHOLDER SCAN ENGINE.
 *
 * This exists only so the interface can be built and reviewed before the real
 * engine lands. It returns fixed findings and talks to nothing. Replace the
 * whole module with a call to POST /api/scan once the backend exists, and
 * delete this file. Nothing in here should ever be shown to the panel as a
 * real result.
 */
import type { Finding, ScanOptions, ScanResult } from './types';

const SAMPLE_FINDINGS: Finding[] = [
  {
    id: 'f1',
    title: 'Sign-up form accepts an empty password and returns a server error',
    category: 'bug',
    severity: 'critical',
    confidence: 'high',
    location: '/signup',
    selector: 'form#signup button[type="submit"]',
    evidence:
      'Submitting the form with an email filled in and the password field left blank returned a 500 response and a blank white page. No validation message was shown before submission.',
    steps: [
      'Open /signup',
      'Enter a valid email address',
      'Leave the password field empty',
      'Press Create account',
    ],
    suggestion:
      'Require a password on the client before submitting, and have the server return a 400 with a readable message instead of a 500.',
  },
  {
    id: 'f2',
    title: 'Session is not cleared on logout, back button restores the dashboard',
    category: 'bug',
    severity: 'high',
    confidence: 'medium',
    location: '/dashboard',
    evidence:
      'After pressing Log out the app redirected to /login, but pressing the browser back button rendered the dashboard again with the previous user data still visible.',
    steps: ['Log in', 'Open the dashboard', 'Press Log out', 'Press the browser back button'],
    suggestion:
      'Invalidate the session server side on logout and add a no-store cache header to authenticated pages.',
  },
  {
    id: 'f3',
    title: 'Primary button text fails contrast guidance against its background',
    category: 'improvement',
    severity: 'medium',
    confidence: 'high',
    location: '/',
    selector: '.btn-primary',
    evidence:
      'Measured contrast ratio between the button label and its background is 3.1 to 1. The usual threshold for body sized text is 4.5 to 1.',
    steps: ['Open the home page', 'Inspect the primary call to action button'],
    suggestion: 'Darken the button background or switch the label to white to clear the threshold.',
  },
  {
    id: 'f4',
    title: 'No loading state while the search results are fetched',
    category: 'improvement',
    severity: 'low',
    confidence: 'medium',
    location: '/search',
    evidence:
      'After submitting a search the page stayed visually identical for around two seconds before results appeared, with no spinner, skeleton or disabled state on the button.',
    steps: ['Open /search', 'Type any term', 'Press Search and watch the page'],
    suggestion:
      'Disable the search button and show a skeleton list while the request is in flight, so a slow network does not read as a broken page.',
  },
];

export type ProgressHandler = (message: string) => void;

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

export async function runMockScan(
  targetUrl: string,
  options: ScanOptions,
  onProgress: ProgressHandler,
): Promise<ScanResult> {
  const startedAt = new Date().toISOString();

  const steps = [
    `Opening ${targetUrl}`,
    'Reading the page structure',
    'Mapping the main flows',
    'Trying sign-up with unusual input',
    'Checking what happens after logout',
    'Looking for things that could be better',
    'Filtering out low confidence noise',
  ];

  for (const step of steps) {
    onProgress(step);
    await wait(650);
  }

  const findings = SAMPLE_FINDINGS.filter((finding) => {
    if (finding.category === 'bug') return options.findBugs;
    return options.findImprovements;
  });

  return {
    targetUrl,
    startedAt,
    finishedAt: new Date().toISOString(),
    pagesVisited: Math.min(options.maxPages, 4),
    findings,
  };
}
