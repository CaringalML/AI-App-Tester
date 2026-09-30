import { useEffect, useState, type ReactNode } from 'react';

/*
 * Plain text with every http(s) address turned into a link that opens in a new
 * tab, followed by a small copy button. Only http and https are linked: the
 * text comes from Claude and from the site under test, so anything else
 * (javascript:, data:) stays inert text.
 */

const URL_PATTERN = /https?:\/\/[^\s<>"'`]+/g;
/** Sentence punctuation that follows a URL in prose rather than belonging to it. */
const TRAILING = /[.,;:!?)\]}'"»]+$/;

function CopyLink({ url }: { url: string }) {
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), 1500);
    return () => clearTimeout(timer);
  }, [copied]);

  return (
    <button
      type="button"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(url);
          setCopied(true);
        } catch {
          // Clipboard blocked; the link itself still works.
        }
      }}
      aria-label={copied ? 'Link copied' : `Copy ${url}`}
      title={copied ? 'Copied' : 'Copy link'}
      className={`mx-0.5 inline-grid size-5.5 translate-y-[3px] place-items-center rounded-md align-baseline transition ${
        copied ? 'text-success' : 'text-faint hover:bg-raised hover:text-ink'
      }`}
    >
      <svg
        className="size-3.5"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        {copied ? (
          <path d="M5 12.5l4.5 4.5L19 7.5" />
        ) : (
          <>
            <rect x="9" y="9" width="11" height="11" rx="2" />
            <path d="M5 15V6a2 2 0 0 1 2-2h8" />
          </>
        )}
      </svg>
    </button>
  );
}

export function LinkedText({ text }: { text: string }) {
  const parts: ReactNode[] = [];
  let last = 0;
  for (const match of text.matchAll(URL_PATTERN)) {
    const url = match[0].replace(TRAILING, '');
    const start = match.index ?? 0;
    if (start > last) parts.push(text.slice(last, start));
    parts.push(
      <span key={start} className="whitespace-normal">
        <a
          href={url}
          target="_blank"
          rel="noopener noreferrer"
          className="text-accent underline decoration-accent/40 underline-offset-2 transition hover:decoration-accent"
        >
          {url}
        </a>
        <CopyLink url={url} />
      </span>,
    );
    last = start + url.length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return <>{parts}</>;
}
