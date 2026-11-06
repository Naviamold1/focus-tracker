# Focus Tracker browser extension

Chrome/Edge MV3, background service worker only. Sends `BrowserEvent` JSON (spec 3.3) to `ws://127.0.0.1:8765/ws/browser`.

## Build and load

```
cd extension
pnpm install
pnpm build          # tsc -> extension/dist/
```

Open `chrome://extensions` (or `edge://extensions`), enable Developer mode, "Load unpacked", pick this `extension/` folder. `manifest.json` lives here and points at `dist/background.js`.
To report incognito/InPrivate tabs, enable "Allow in incognito" on the extension; events then carry `incognito: true` and the engine treats them as private.

## Behavior

- Triggers: tab activated, tab updated (load complete, title, audible), window focus change, plus a 2 s heartbeat of the last state.
- URL: query, fragment and credentials stripped, path capped at 3 segments. Domain is eTLD+1 by a small heuristic (co.uk style). chrome://, edge://, about:, file:, new tab give null url, domain and title.
- Reconnect: exponential backoff 1 s to 30 s; errors are swallowed.
- Optional setting: `engineUrl` in `chrome.storage.sync` (default above).
- `src/chrome.d.ts` is a hand-written minimal typing of the chrome APIs used (keeps typescript the only dependency).

## Keepalive choice

MV3 workers sleep after 30 s idle. Chrome 116+ resets that timer on WebSocket traffic, so the 2 s heartbeat over the open socket keeps the worker alive while the engine is up (hence `minimum_chrome_version` 116). When the engine is down there is no socket traffic, so a 30 s `chrome.alarms` alarm wakes the worker and retries the connection.
