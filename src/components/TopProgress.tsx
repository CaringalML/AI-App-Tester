import { useEffect, useRef, useState } from 'react';
import type { ScanPhase } from '../lib/types';

/*
 * A thin bar pinned to the top of the window, visible wherever the page is
 * scrolled. Its width is the server's real completion figure, never a fake
 * trickle; the moving sheen is what shows it is still alive during long
 * model turns. It fills and fades out when the test finishes.
 *
 * Only shown for runs watched live: reopening a finished test from history
 * goes straight to its results without a bar.
 */
export function TopProgress({ phase, completion }: { phase: ScanPhase; completion: number }) {
  const [visible, setVisible] = useState(false);
  const wasRunning = useRef(false);

  useEffect(() => {
    if (phase === 'running') {
      wasRunning.current = true;
      setVisible(true);
      return;
    }
    if (wasRunning.current && (phase === 'done' || phase === 'error')) {
      wasRunning.current = false;
      const timer = setTimeout(() => setVisible(false), 1400);
      return () => clearTimeout(timer);
    }
    wasRunning.current = false;
    setVisible(false);
  }, [phase]);

  const percent = phase === 'done' ? 100 : Math.max(2, Math.min(100, completion * 100));

  return (
    <div
      role="progressbar"
      aria-label="Test progress"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(percent)}
      aria-hidden={!visible}
      className={`pointer-events-none fixed inset-x-0 top-0 z-50 h-[3px] transition-opacity duration-500 ${
        visible ? 'opacity-100' : 'opacity-0'
      }`}
    >
      <div
        className={`relative h-full overflow-hidden rounded-r-full transition-[width] duration-700 ease-out ${
          phase === 'error' ? 'bg-critical' : 'bg-accent'
        } shadow-[0_0_12px_var(--accent)]`}
        style={{ width: `${percent}%` }}
      >
        {phase === 'running' ? (
          <span
            className="absolute inset-0 animate-shimmer bg-linear-to-r from-transparent via-white/60 to-transparent"
            style={{ backgroundSize: '200% 100%' }}
          />
        ) : null}
      </div>
    </div>
  );
}
