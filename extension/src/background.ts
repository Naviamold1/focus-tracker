import { domainOf, sanitizeUrl } from "./url.js";

const DEFAULT_ENGINE_URL = "ws://127.0.0.1:8765/ws/browser";
const HEARTBEAT_MS = 2000;
const BACKOFF_START_MS = 1000;
const BACKOFF_CAP_MS = 30_000;
const ALARM = "keepalive";

type BrowserEvent = {
  v: 1;
  url: string | null;
  domain: string | null;
  pageTitle: string | null;
  audible: boolean;
  incognito: boolean;
  ts: number;
};

let socket: WebSocket | null = null;
let backoffMs = BACKOFF_START_MS;
let retryTimer: ReturnType<typeof setTimeout> | undefined;
let heartbeat: ReturnType<typeof setInterval> | undefined;
let last: BrowserEvent | null = null;

/** Reads the optional engine URL override from storage.sync. */
async function engineUrl(): Promise<string> {
  try {
    const stored = await chrome.storage.sync.get("engineUrl");
    const value = stored["engineUrl"];
    return typeof value === "string" && value ? value : DEFAULT_ENGINE_URL;
  } catch {
    return DEFAULT_ENGINE_URL;
  }
}

/** Sends the last known event with a fresh timestamp; silently drops if disconnected. */
function flush(): void {
  if (!last || socket?.readyState !== WebSocket.OPEN) return;
  try {
    socket.send(JSON.stringify({ ...last, ts: Date.now() }));
  } catch {
    // socket is closing; the close handler schedules a reconnect
  }
}

/** Schedules a reconnect with exponential backoff (1 s doubling to 30 s). */
function scheduleReconnect(): void {
  clearTimeout(retryTimer);
  retryTimer = setTimeout(() => void connect(), backoffMs);
  backoffMs = Math.min(backoffMs * 2, BACKOFF_CAP_MS);
}

/** Opens the engine socket if not already open or connecting. Never throws. */
async function connect(): Promise<void> {
  if (socket && socket.readyState <= WebSocket.OPEN) return;
  try {
    const ws = new WebSocket(await engineUrl());
    socket = ws;
    ws.onopen = () => {
      backoffMs = BACKOFF_START_MS;
      flush();
    };
    ws.onclose = () => {
      if (socket === ws) socket = null;
      scheduleReconnect();
    };
    ws.onerror = () => ws.close();
  } catch {
    socket = null;
    scheduleReconnect();
  }
}

/** Builds the event for a tab; non-web pages yield null url, domain and title. */
function toEvent(tab: chrome.tabs.Tab): BrowserEvent {
  const domain = domainOf(tab.url);
  return {
    v: 1,
    url: sanitizeUrl(tab.url),
    domain,
    pageTitle: domain === null ? null : (tab.title ?? null),
    audible: tab.audible ?? false,
    incognito: tab.incognito,
    ts: Date.now(),
  };
}

/** Re-reads the active tab of the focused window and sends it. */
async function refresh(): Promise<void> {
  try {
    const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
    if (!tab) return;
    last = toEvent(tab);
    flush();
  } catch {
    // tab vanished mid-query; the next event will fix it
  }
}

/** Starts the 2 s heartbeat. Traffic on an open socket also resets the worker idle timer (Chrome 116+). */
function startHeartbeat(): void {
  clearInterval(heartbeat);
  heartbeat = setInterval(flush, HEARTBEAT_MS);
}

chrome.tabs.onActivated.addListener(() => void refresh());
chrome.tabs.onUpdated.addListener((_id, info, tab) => {
  const relevant = info.status === "complete" || info.title !== undefined || info.audible !== undefined;
  if (tab.active && relevant) void refresh();
});
chrome.windows.onFocusChanged.addListener(() => void refresh());

// Safety net: if the worker was suspended anyway, the alarm wakes it and reconnects.
chrome.alarms.create(ALARM, { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener(() => {
  startHeartbeat();
  void connect().then(refresh);
});

startHeartbeat();
void connect().then(refresh);
