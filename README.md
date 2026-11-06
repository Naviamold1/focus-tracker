# Focus Tracker

Local focus assistant for Windows and Linux (X11). Watches the foreground app, the active browser tab (via the extension), and head pose (camera, local only), judges what you are looking at against a declared session goal, and nudges when you drift. See `spec.md`.

## Run

```
uv run python -m focus_tracker
```

Opens the Qt desktop window. Set a goal first; with no goal the engine only observes. For debugging without a window (logs each tick):

```
uv run python -c "from focus_tracker.main import run_headless; run_headless()"
```

The browser extension connects to `ws://127.0.0.1:8765/ws/browser`; the camera preview stays inside the window and is never saved or sent. Pick the capture device from the dropdown above the camera view (Refresh picks up newly plugged cameras); the choice is saved. Virtual cameras such as OBS Virtual Camera are listed too, so check you picked a real webcam if no face is found.

`TYPESAFE_API_KEY` goes in `.env` (cwd or the data dir below). Without it the classifier runs on the local tier only. Config and logs live in `%APPDATA%\focus-tracker` on Windows and `$XDG_DATA_HOME/focus-tracker` (default `~/.local/share/focus-tracker`) on Linux. Override with `FOCUS_TRACKER_DATA`.

## Linux

`uv sync` installs `python-xlib` instead of `pywin32` on Linux. Needs a desktop with Qt's xcb plugin deps (e.g. `libxcb-cursor0` on Debian/Ubuntu).

- X11 sessions: full support. Foreground app and title come from EWMH (`_NET_ACTIVE_WINDOW`), idle from the XScreenSaver extension. Under XWayland only X clients are visible; focusing a native Wayland window reads as no app.
- Native Wayland (no `DISPLAY`): there is no generic foreground-window API, so the app signal is empty and a warning is logged once. Idle is best effort via GNOME Mutter (`gdbus`), otherwise 0. Run the app and your browser under X11 or XWayland for full behaviour.
- Global hotkeys use the `keyboard` package, which needs root on Linux, so they are off for normal users (info line in the log). The window buttons do the same thing.
- Escalation toasts use `notify-send` (libnotify) when on `PATH`, otherwise they are skipped silently.
- Camera uses V4L2. The browser extension works unchanged.

## Layout

- `watcher/` foreground app + input idle: `_win32.py` (pywin32), `_x11.py` (python-xlib, EWMH + XScreenSaver), `_wayland.py` (best effort)
- `fusion.py` app + browser event -> `Surface`, 2 s switch debounce
- `decision.py` state machine, PI, nudge ladder (pure `step(inputs, now_ms)`)
- `main.py` `Runtime` wiring, 2 s tick loop, commands; builds a `UiState` per tick
- `engine_thread.py` runs the runtime and the extension server on a background thread for the Qt window
- `server.py` FastAPI app with only `/ws/browser`
- `demo.py` D / G / R scenarios, `toast.py` escalation toasts
- `vision/` webcam presence and head pose; `latest_frame()` feeds the camera view
- `ui/` PySide6 window

## Nudge ladder

Soft nudge after 60 s on a distraction while facing the screen (x2 for confidence 0.5 to 0.8 or local-tier verdicts, half for stale answers). If still distracted 2 min later, a desktop toast (never for stale answers). One nudge per surface per 5 min, 3 per hour, snooze 10 min. Pending classifications never nudge. Returning to productive work resets the dwell silently.

## Demo

Buttons in the window, or global hotkeys Ctrl+Alt+D / G / R (Windows, or root on Linux):

- D replays a 20 minute distraction arc at 16x through the real engine, no camera needed
- G sets the goal "editing the demo reel" and opens an unseen entertainment site
- R resets PI and clock

## Test

```
uv run pytest
uv run ruff check src/focus_tracker tests
```
