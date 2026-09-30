import type { ReactNode } from 'react';
import { CopyButton } from './CopyButton';

/*
 * Plain text with every http(s) address turned into a link that opens in a new
 * tab, followed by a small copy button. Only http and https are linked: the
 * text comes from Claude and from the site under test, so anything else
 * (javascript:, data:) stays inert text.
 */

const URL_PATTERN = /https?:\/\/[^\s<>"'`]+/g;
/** Sentence punctuation that follows a URL in prose rather than belonging to it. */
const TRAILING = /[.,;:!?)\]}'"»]+$/;

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
        <CopyButton
          text={url}
          label={url}
          className="mx-0.5 size-5.5 translate-y-[3px] align-baseline"
        />
      </span>,
    );
    last = start + url.length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return <>{parts}</>;
}
