import { useEffect, useState } from 'react';

type Theme = 'dark' | 'light';

function readInitial(): Theme {
  try {
    const stored = localStorage.getItem('ai-app-tester:theme');
    if (stored === 'dark' || stored === 'light') return stored;
  } catch {
    // Private windows and blocked site data both land here. Fall through.
  }
  return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
}

/** Present because nobody knows how bright the room will be on demo day. */
export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(readInitial);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem('ai-app-tester:theme', theme);
    } catch {
      // Storage is a convenience here, never a requirement.
    }
  }, [theme]);

  return (
    <button
      type="button"
      className="grid size-8.5 place-items-center rounded-[9px] border border-line bg-surface text-muted transition-colors hover:border-line-strong hover:text-ink"
      aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}
      onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
    >
      {theme === 'dark' ? (
        <svg
          className="size-4"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={1.7}
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <circle cx="12" cy="12" r="4" />
          <path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M19.1 4.9l-1.4 1.4M6.3 17.7l-1.4 1.4" />
        </svg>
      ) : (
        <svg
          className="size-4"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={1.7}
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />
        </svg>
      )}
    </button>
  );
}
