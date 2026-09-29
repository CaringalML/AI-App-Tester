import { useState } from 'react';
import type { Finding } from '../lib/types';

/*
 * The Playwright test exported for one finding. The code was assembled on the
 * server from the recorded run, not written by the model, and its status says
 * whether its assertion was checked against the live page.
 */

const STATUS: Record<
  NonNullable<Finding['testStatus']>,
  { label: string; className: string; icon: string }
> = {
  'fails-now': {
    label: 'Fails today, as it should',
    className: 'border-low/40 text-low',
    icon: 'M5 12l4 4L19 6',
  },
  'passes-now': {
    label: 'Already passes, may not catch this',
    className: 'border-medium/40 text-medium',
    icon: 'M12 8v5M12 16.5v.5',
  },
  unverified: {
    label: 'Not checked automatically',
    className: 'border-line-strong text-faint',
    icon: 'M12 8v5M12 16.5v.5',
  },
};

function fileName(finding: Finding): string {
  const slug = finding.title
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '')
    .slice(0, 48);
  return `${finding.id}-${slug || 'finding'}.spec.ts`;
}

export function RegressionTest({ finding }: { finding: Finding }) {
  const [copied, setCopied] = useState(false);
  if (!finding.playwrightTest) return null;
  const status = STATUS[finding.testStatus ?? 'unverified'];

  async function copy() {
    try {
      await navigator.clipboard.writeText(finding.playwrightTest ?? '');
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard can be blocked; the code is still visible and downloadable.
    }
  }

  function download() {
    const blob = new Blob([finding.playwrightTest ?? ''], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = fileName(finding);
    link.click();
    URL.revokeObjectURL(url);
  }

  const button =
    'rounded-md border border-line-strong px-2.5 py-1 text-[12px] text-muted transition hover:border-accent/40 hover:text-ink';

  return (
    <section>
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <h4 className="text-[11.5px] font-semibold tracking-[0.06em] text-faint uppercase">
          Regression test · Playwright
        </h4>
        <span
          className={`flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] ${status.className}`}
          title={finding.testNote ?? undefined}
        >
          <svg
            className="size-3"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth={2.5}
            strokeLinecap="round"
            aria-hidden="true"
          >
            <path d={status.icon} />
          </svg>
          {status.label}
        </span>
        <span className="ml-auto flex gap-1.5">
          <button type="button" className={button} onClick={copy}>
            {copied ? 'Copied' : 'Copy'}
          </button>
          <button type="button" className={button} onClick={download}>
            Download .spec.ts
          </button>
        </span>
      </div>
      {finding.testNote ? <p className="mb-2 text-[12px] text-faint">{finding.testNote}</p> : null}
      <pre className="max-h-72 overflow-auto rounded-lg border border-line bg-bg p-3 font-mono text-[11.5px] leading-relaxed text-muted">
        {finding.playwrightTest}
      </pre>
    </section>
  );
}
