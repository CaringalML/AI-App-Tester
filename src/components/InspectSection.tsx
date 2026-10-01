import { useState } from 'react';
import type { InspectTarget, StyleSource } from '../lib/types';
import { CopyButton } from './CopyButton';

/*
 * How a developer finds the problem in the page's code: right-click the element,
 * choose Inspect, then read the Styles pane and follow a rule to its file in
 * Sources. Each flagged element gets those steps, the HTML the Elements panel
 * shows, and, where known, the CSS rules that set its colours.
 */

const heading = 'mb-1 text-[11px] font-semibold tracking-[0.06em] text-faint uppercase';

function name(target: InspectTarget): string {
  return target.text ? `“${target.text}”` : 'the element';
}

function StyleRow({ style }: { style: StyleSource }) {
  const note = style.element
    ? `painted by ${style.element}`
    : style.inherited
      ? 'inherited from a parent'
      : null;
  return (
    <li className="grid gap-x-3 gap-y-0.5 sm:grid-cols-[minmax(0,1fr)_auto]">
      <span className="min-w-0 font-mono text-[12.5px]">
        <span className="text-faint">
          {style.rule} {'{'}{' '}
        </span>
        <span className="text-ink">
          {style.name}: {style.value};
        </span>
        <span className="text-faint"> {'}'}</span>
      </span>
      <span className="flex flex-wrap items-center gap-x-2 text-[12px] text-faint sm:justify-end">
        {note ? <span>{note}</span> : null}
        {style.url ? (
          <a
            href={style.url}
            target="_blank"
            rel="noopener noreferrer"
            className="font-mono text-accent underline decoration-accent/40 underline-offset-2 hover:decoration-accent"
            title="Open the stylesheet"
          >
            {style.source}
          </a>
        ) : (
          <span className="font-mono">{style.source}</span>
        )}
      </span>
    </li>
  );
}

export function InspectSection({ targets }: { targets: InspectTarget[] }) {
  const [pick, setPick] = useState(0);
  const target = targets[Math.min(pick, targets.length - 1)];

  return (
    <section>
      <h4 className="mb-1.25 text-[11.5px] font-semibold tracking-[0.06em] text-faint uppercase">
        Inspect it in your browser
      </h4>

      {targets.length > 1 ? (
        <div className="mb-2 flex flex-wrap gap-1.5" role="tablist" aria-label="Examples">
          {targets.map((t, index) => (
            <button
              key={t.selector}
              type="button"
              role="tab"
              aria-selected={index === pick}
              onClick={() => setPick(index)}
              className={`max-w-56 truncate rounded-full border px-2.5 py-0.5 text-[12px] transition ${
                index === pick
                  ? 'border-accent/50 bg-accent/10 text-accent'
                  : 'border-line-strong text-muted hover:text-ink'
              }`}
            >
              {t.text || t.selector}
            </button>
          ))}
        </div>
      ) : null}

      <ol className="grid list-decimal gap-1 pl-4.5 text-sm text-muted">
        <li>
          On the page, right-click {name(target)} and choose <b className="text-ink">Inspect</b> (or
          press Ctrl+Shift+C, ⌘⌥C on a Mac, then click it).
        </li>
        <li>
          Not selected? In the Elements panel press Ctrl+F (⌘F) and paste{' '}
          <code className="rounded border border-line bg-bg px-1 py-px font-mono text-[12px] text-ink">
            {target.selector}
          </code>
          <CopyButton
            key={target.selector}
            text={target.selector}
            label="the selector"
            className="ml-1 size-5.5 translate-y-[3px] align-baseline"
          />
        </li>
        {target.styles.length ? (
          <li>
            In the <b className="text-ink">Styles</b> pane, these rules set its colours. Click a
            file name to open it in <b className="text-ink">Sources</b>.
          </li>
        ) : null}
      </ol>

      <div className="mt-2.5 grid gap-2.5 rounded-lg border border-line bg-bg px-3 py-2.5">
        <div>
          <p className={heading}>Element</p>
          <pre className="font-mono text-[12px] leading-relaxed [overflow-wrap:anywhere] whitespace-pre-wrap text-muted">
            {target.html}
          </pre>
        </div>
        {target.styles.length ? (
          <div>
            <p className={heading}>Styles</p>
            <ul className="grid gap-1.5">
              {target.styles.map((style) => (
                <StyleRow key={`${style.name}-${style.rule}`} style={style} />
              ))}
            </ul>
          </div>
        ) : null}
      </div>
    </section>
  );
}
