import type { Finding } from './types';

/*
 * One finding as a GitHub-flavoured Markdown issue, so a developer can paste it
 * straight into GitHub, GitLab, Jira or Slack. Everything is text: screenshot
 * links from the scan are signed and expire, so they are left out rather than
 * posted into a tracker where they would break later.
 */

const TEST_STATUS: Record<NonNullable<Finding['testStatus']>, string> = {
  'fails-now': 'fails now, passes once fixed',
  'passes-now': 'passes now, guards against a regression',
  unverified: 'not replay-verified',
};

function cap(word: string): string {
  return word.charAt(0).toUpperCase() + word.slice(1);
}

/** A page path from the finding, made absolute against the tested site. */
function pageUrl(location: string, targetUrl?: string): string {
  const first = location.split(/[,\s]/)[0];
  if (!targetUrl || !first) return location;
  try {
    return new URL(first, targetUrl).href;
  } catch {
    return location;
  }
}

export interface IssueContext {
  targetUrl?: string;
  /** The command log step that shows it, when there is one. */
  step?: number;
}

export function findingAsIssue(finding: Finding, context: IssueContext = {}): string {
  const bug = finding.category === 'bug';
  const url = pageUrl(finding.location, context.targetUrl);
  const lines: string[] = [
    `## ${bug ? '[Bug]' : '[Improvement]'} ${finding.title}`,
    '',
    `**Severity:** ${cap(finding.severity)} · **Confidence:** ${cap(finding.confidence)} · **Found by:** ${
      finding.source === 'automated' ? 'browser check' : 'Claude, exploring the app'
    }`,
    `**Page:** ${url}${finding.selector ? ` · **Element:** \`${finding.selector}\`` : ''}`,
    '',
    '### What happened',
    '',
    ...finding.evidence.split('\n').map((line) => `> ${line}`),
    '',
    '### Steps to reproduce',
    '',
    ...finding.steps.map((step, index) => `${index + 1}. ${step}`),
    '',
    `### ${bug ? 'Suggested fix' : 'Suggestion'}`,
    '',
    finding.suggestion,
  ];

  if (finding.playwrightTest) {
    const status = finding.testStatus ? ` (${TEST_STATUS[finding.testStatus]})` : '';
    lines.push(
      '',
      '<details>',
      `<summary>Playwright regression test${status}</summary>`,
      '',
      '```ts',
      finding.playwrightTest.trimEnd(),
      '```',
      '',
      '</details>',
    );
  }

  lines.push(
    '',
    '---',
    `_Reported by AI App Tester${context.step !== undefined ? `, step ${context.step} of the run` : ''}._`,
  );
  return lines.join('\n');
}
