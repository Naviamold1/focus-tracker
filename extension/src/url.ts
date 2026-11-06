const MAX_PATH_SEGMENTS = 3;

// Second-level labels that form a public suffix under a country code (co.uk, com.au, ...).
const SLD = new Set(["co", "com", "org", "net", "gov", "edu", "ac", "ne", "or", "go"]);

/** Parses http(s) URLs only; chrome://, edge://, about:, file:, newtab return null. */
function parseWeb(raw: string | undefined): URL | null {
  if (!raw) return null;
  try {
    const u = new URL(raw);
    return u.protocol === "http:" || u.protocol === "https:" ? u : null;
  } catch {
    return null;
  }
}

/** eTLD+1 via a small heuristic (handles co.uk style suffixes); IPs pass through. */
export function registrableDomain(host: string): string {
  if (host.includes(":") || /^\d+(\.\d+){3}$/.test(host)) return host;
  const parts = host.toLowerCase().split(".");
  if (parts.length <= 2) return parts.join(".");
  const tld = parts[parts.length - 1] ?? "";
  const sld = parts[parts.length - 2] ?? "";
  const keep = tld.length === 2 && SLD.has(sld) ? 3 : 2;
  return parts.slice(-keep).join(".");
}

/** Strips query, fragment and credentials; caps path depth. */
export function sanitizeUrl(raw: string | undefined): string | null {
  const u = parseWeb(raw);
  if (!u) return null;
  const path = u.pathname.split("/").filter(Boolean).slice(0, MAX_PATH_SEGMENTS).join("/");
  return `${u.protocol}//${u.host}/${path}`;
}

export function domainOf(raw: string | undefined): string | null {
  const u = parseWeb(raw);
  return u ? registrableDomain(u.hostname) : null;
}
