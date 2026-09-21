/** Accepts what a person would actually type and turns it into a real URL. */
export function normalizeUrl(raw: string): string {
  const trimmed = raw.trim();
  if (!trimmed) return '';
  if (/^https?:\/\//i.test(trimmed)) return trimmed;
  return `https://${trimmed}`;
}

export interface UrlCheck {
  ok: boolean;
  /** Null when ok, otherwise a message written for a person, not a parser. */
  message: string | null;
  normalized: string;
}

export function checkUrl(raw: string): UrlCheck {
  const normalized = normalizeUrl(raw);

  if (!normalized) {
    return { ok: false, message: 'Enter the address of the app you want tested.', normalized };
  }

  let parsed: URL;
  try {
    parsed = new URL(normalized);
  } catch {
    return { ok: false, message: 'That does not look like a web address.', normalized };
  }

  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
    return { ok: false, message: 'Only http and https addresses can be tested.', normalized };
  }

  const isLocal =
    parsed.hostname === 'localhost' ||
    parsed.hostname === '127.0.0.1' ||
    parsed.hostname.endsWith('.local');

  if (!isLocal && !parsed.hostname.includes('.')) {
    return { ok: false, message: 'That address is missing a domain, for example .com or .nz.', normalized };
  }

  return { ok: true, message: null, normalized };
}
