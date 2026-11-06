# Focus Tracker — Specification

**Version:** 0.4 (Draft — supersedes 0.3)
**Status:** Planning
**Last updated:** 2026-11-06

> **0.4 changelog:** (1) The offline tier is now a **local sentence-embedding model** scored against the goal, replacing the hardcoded rules list (§4.2.5). (2) The session goal is **required for nudging**; with no goal the engine only observes (§4.2.1). (3) New **never-send list** (incognito, password managers, banking, user-excluded surfaces) keeps sensitive titles off the wire (§4.2.6, §5). (4) Classification sits behind a thin `classify(goal, surface)` interface; Jev is the default implementation, not a hard dependency (§4.2.2). (5) Camera and head-pose tracking stay in v1 as the core "wow" feature. Scope: hackathon project, but the camera tracker, the Jev classifier and the PC task classifier must all work end to end. Unresolved items from the design review are in §10.
>
> **0.3 changelog:** hardcoded category lists demoted to a fallback tier. Surface classification is now delegated to **Jev** (TypeSafe's System One model — typed questions over a state, structured answers with calibrated probabilities and confidence; docs: https://docs.typesafe.ai). Added the **session goal** concept: distraction is defined against what the user says they are working on, not a static site dictionary. Added classification cache, confidence routing, offline rules fallback, and cost/latency guards. Privacy contract refined: text metadata (titles, domains, goal) may be sent to the Jev API; **camera frames and screen pixels never leave the machine**.

A local-first focus assistant that combines **whole-screen app activity**, **browser URL detail**, and **visual presence/direction** — with a semantic layer deciding what counts as distraction. Posture and distance tracking remain out of scope.

---

## 1. Problem & Goal

People drift into distraction without noticing. Existing blockers are punitive ("you can't visit YouTube") and brittle; screen-time dashboards are retrospective and ignored. Legalistic site blocklists are trivially wrong: the same URL can be research, procrastination, or work depending on the hour and the task.

**Goal:** A lightweight, personal companion that:

1. Understands *what the user is actually looking at* — any app on the machine, browser included — and judges it against the user's session goal.
2. Detects when the user is away (no keyboard/mouse, no face) and pauses the session clock.
3. Stays silent during deep work and during "offline thinking" (distracting surface + not facing screen = probably reading or sketching — do not nag).

**North-star demo moment:** declare the goal "editing my demo video" → open YouTube → **no nudge** (Jev: editing-related content, `productive`); flip the goal to "write spec" → same YouTube tab now warms up toward a nudge. And a plain novel domain the dictionary has never seen gets correctly judged in seconds.

---

## 2. Non-Goals (explicitly cut)

| Feature | Status |
|---|---|
| Posture detection | ❌ Cut |
| Distance-to-screen estimation | ❌ Cut |
| Eye-gaze / per-pupil tracking | ❌ Cut — head pose only |
| Pixel-level screen capture (Rewind.ai-style screen relay + OCR/VLM) | ❌ Cut — CPU/privacy cost; also **Jev receives no images by design**, only text metadata |
| **Upload of camera frames or screenshots anywhere, ever** | ❌ Cut — vision output is yaw/pitch/booleans, computed locally by MediaPipe/OpenCV |
| Accounts, multi-user sync | ❌ Cut — single local user |
| Blocking sites/apps outright | ❌ Cut — nudges only, no enforcement |
| macOS support | ❌ Cut in v1. Windows (`pywin32`) and Linux are supported. On Linux, X11 is full support (EWMH + XScreenSaver via `python-xlib`); native Wayland is best-effort: idle only, no foreground app, because Wayland has no generic API for the focused window |

---

## 3. Architecture

### 3.1 Chosen design: one Python engine + a thin browser sensor + Jev as the semantic layer

```
┌──────────────────────────────┐        ┌─────────────────────────────────────────────┐
│   Browser Extension (MV3)    │   ws   │        Focus Engine (Python)                │
│   "browser sensor" only:     │───────►│                                             │
│   • active tab URL + domain  │  1 Hz  │  ┌───────────────────────────────────────┐  │
│   • audible flag             │        │  │  App Watcher (thread, 1 Hz)           │  │
│   • nothing else             │        │  │  pywin32: foreground app + title,     │  │
└──────────────────────────────┘        │  │  input idle (GetLastInputInfo)        │  │
                                        │  └──────────────┬────────────────────────┘  │
                                        │  ┌──────────────▼────────────────────────┐  │
                                        │  │  Vision (thread, 5–10 FPS)            │  │
                                        │  │  OpenCV → MediaPipe → pres/facing(°)  │  │
                                        │  │  (never leaves the machine)           │  │
                                        │  └──────────────┬────────────────────────┘  │
                                        │  ┌──────────────▼────────────────────────┐  │
                                        │  │  Classifier (async + cache)           │  │
                                        │  │  Jev Choice/Noul over {goal, surface} │  │
                                        │  └──────────────┬────────────────────────┘  │
                                        │  ┌──────────────▼────────────────────────┐  │
                                        │  │  Decision Engine (2 s tick)           │  │
                                        │  │  state machine + PI + nudges          │  │
                                        │  │  (owns confidence routing)            │  │
                                        │  └──────────────┬────────────────────────┘  │
                                        │  ┌──────────────▼────────────────────────┐  │
                                        │  │  Dashboard (localhost:8765)           │  │
                                        │  │  status, goal editor, PI, settings    │  │
                                        │  └───────────────────────────────────────┘  │
                                        └───────────────────────┬─────────────────────┘
                                                                │ HTTPS (text only)
                                                                ▼
                                                     api.typesafe.ai  (Jev, jev-latest)
```

**Why this shape:**
- The extension's only irreplaceable job is reading the **real URL** of the active tab (window titles of Chrome/Edge are ambiguous). Everything else moves to the engine.
- Windows-only watcher is ~40 lines of pywin32; 1 Hz polling costs **< 0.1% CPU**.
- Jev's request shape maps 1:1 onto our decision layer: one batched System One request per novel surface asks *category* (Choice) and *goal-alignment* (Noul) in parallel — and its own confidence tells the engine whether to act, follow up, or fall back (the documented [confidence routing](https://docs.typesafe.ai/confidence.md) and [Companion Noul](https://docs.typesafe.ai/patterns/companion-noul.md) patterns).
- Vision stays fully local; the Jev call carries **only** the pieces of state a human-written note could carry: app name, window/page title, domain, and the declared session goal.

### 3.2 Component responsibilities

| Component | Responsibility |
|---|---|
| **Extension** (background only) | `tabs.onActivated` / `tabs.onUpdated`; sends `{activeUrl, activeDomain, pageTitle, audible}` over WS every ≥2 s dwell or on change; reconnects with backoff when the engine is down |
| **App watcher thread** | 1 Hz poll: process name, window title, `GetLastInputInfo` → publishes `AppSignal` |
| **Vision thread** | `cv2.VideoCapture(DSHOW)` at 30 FPS capture, inference throttled to 5–10 FPS; MediaPipe Face Landmarker; `cv2.solvePnP` head pose → `present` + `yaw°`/`pitch°`; hysteresis + flap suppression |
| **Classifier** | Async Jev requests for novel surfaces; batched questions; confidence/probability routing; persistent result cache; local embedding tier when Jev is offline/unreachable/low-confidence; never-send filter |
| **Decision engine** | 2 s tick fusion: state machine (§4.3), PI accounting, nudge scheduling, anti-annoyance caps |
| **Dashboard** | Served at `localhost:8765`; live status strip, **goal editor (first-class control)**, PI chart, threshold sliders, cache inspector, demo buttons |
| **Nudge surfaces** | (a) dashboard banner/toast, (b) Windows toast on escalation only, (c) optional always-on-top overlay strip (stretch) |

### 3.3 Message schema (single source of truth)

```ts
// Extension → Engine (WS, "browser" channel)
type BrowserEvent = {
  v: 1;
  url: string | null;      // full URL, query & fragment STRIPPED (privacy + length)
  domain: string | null;   // eTLD+1, null if chrome:// / newtab
  pageTitle: string | null; // active tab document.title
  audible: boolean;
  ts: number;
}

// Engine internal signals
type AppSignal   = { v: 1; app: string | null; title: string | null; inputIdleSec: number }
type VisionEvent = { v: 1; present: boolean; yaw: number; pitch: number; fps: number }

// Classifier → Decision Engine
type Classification = {
  v: 1;
  key: string;                      // cache key: "browser:youtube.com|8f3a" (domain + title-hash) or "app:Discord|<t>"
  category: "productive" | "neutral" | "distraction" | null;   // null until resolved
  source: "jev" | "cache" | "local" | "override";   // "local" = embedding tier (§4.2.5)
  confidence: number | null;        // 0..1, from Jev
  goalAligned: number | null;       // Noul probability, 0..1
  ts: number;
}

// Engine → dashboard / any UI (WS, "state" channel)
type Tick = {
  v: 1;
  state: "focused" | "distracted" | "nudged" | "afk" | "quiet_thinking";
  surface: { kind: "browser" | "app"; label: string };
  category: "productive" | "neutral" | "distraction" | "unknown" | null;
  categorySource: Classification["source"] | null;    // shown in UI ("Jev · 0.91")
  classificationPending: boolean;                     // true while Jev is answering
  goal: string;
  camera: { on: boolean; present: boolean; yaw: number; pitch: number; degraded: boolean };
  pi: number;
}
```

Versioning rule: every message carries `v`; unknown `v` rejected with one warning.

---

## 4. Feature Specification

### 4.1 Whole-screen app awareness (engine watcher)

1 Hz loop on a daemon thread:

- **Foreground app:** `GetForegroundWindow` → `GetWindowText` + `QueryFullProcessImageName` (normal user perms, no admin) → human label (`Discord`, `Overwatch`, `Acrobat`…).
- **Real input idle:** `GetLastInputInfo` → `inputIdleSec`. The authoritative AFK signal; camera presence is supplementary.
- 2 s dwell debounce before counting a surface switch (kills Alt-Tab flicker).

The watcher is **category-agnostic**: it emits surface facts only; judgment lives in the Classifier (§4.2).

### 4.2 Semantic classification via Jev (replaces hardcoded categories)

**5-second explanation:** send Jev a *state* and a batch of typed *questions*; get back typed answers with per-option probabilities and a confidence figure our code can branch on. One request, several questions, all evaluated independently — so the engine asks as many or as few as it needs per surface.

#### 4.2.1 The session goal (first-class concept)

The user declares a free-text goal in the dashboard: *"write the spec for the hackathon project", "edit the demo reel", "study for the physics exam"*. The goal:

- travels with every Jev request as part of the state;
- is visible in the status bar at all times;
- resets to the last used goal on engine start (goal history dropdown, last 10);
- is **required for nudging**. With no goal set the engine is observe-only: surfaces are tracked and shown, but no Jev calls are made (there is nothing to judge against), category stays `unknown`, no nudges fire, and PI does not move. The dashboard shows a "set a goal to start" prompt. A generic domain-level verdict is deliberately not offered, since that would be the blocklist again.

**This is the whole product thesis:** the model decides distraction *against intention*, not against a list. YouTube-with-an-editing-goal is work; a LeetCode tab while cramming for a biology final is slacking.

#### 4.2.2 Request shape

**Classifier interface.** The rest of the engine sees one function, `classify(goal, surface) -> Classification` (§3.3). Jev is the default implementation; the local embedding tier (§4.2.5) is the second. Nothing outside the Classifier component depends on Jev's response types. Jev is chosen because it works zero-shot against free-text goals with calibrated confidence, so no training data or model training is needed; it is not mandated, and a different LLM can be dropped in behind the same interface.

One System One request per **novel surface** (browser: `domain + page-title hash`; apps: `(app, title-normalized)` — see §4.2.4):

```python
from typesafe_sdk import Choice, Noul, Score, TypeSafeClient

state = {
    "goal": "write the spec for the hackathon project",
    "surface": {
        "kind": "browser",          # or "app"
        "app": "Google Chrome",
        "domain": "youtube.com",    # from extension; null for desktop apps
        "url": "youtube.com/watch/v=abc123",   # query & fragment stripped, depth-capped
        "page_title": "Capacitor charging basics Explained - YouTube",
        "window_title": "Capacitor charging basics Explained - Google Chrome",
    },
}

with TypeSafeClient(model="jev-latest") as client:
    response = client.system_one(
        state=state,
        questions={
            "category": Choice(
                instructions="Classify this surface for someone declared the goal",
                criteria={
                    "productive":  "Genuinely advances the stated goal",
                    "neutral":     "Utilities, logistics, or rest; neither advances nor derails",
                    "distraction": "Entertainment or sidetrack; working against the stated goal",
                },
            ),
            "goal_aligned": Noul(
                instructions="This surface is plausibly required for the stated goal",
            ),
        },
    )
    category = response.choices["category"]       # .choice, .probabilities, .confidence
    aligned  = response.nouls["goal_aligned"]     # .noul
```

Both questions ship in the **same request** (parallel evaluation; adding questions barely costs latency per the [parallel questions](https://docs.typesafe.ai/cookbooks/parallel_questions.md) findings). Request payload is capped at ≤ 600 chars of state text by construction (title truncated, only fields above).

#### 4.2.3 Confidence routing (how the engine trusts the answer)

Following the documented [confidence routing](https://docs.typesafe.ai/patterns/confidence-routing.md) + [probability routing](https://docs.typesafe.ai/patterns/probability-routing.md) patterns:

| Answer | Engine behavior |
|---|---|
| `confidence ≥ 0.8` | Adopt classification; cache it; proceed with the state machine |
| `0.5 ≤ confidence < 0.8` | Adopt, but the nudge dwell **doubles** (hesitancy buys patience); UI shows the confidence figure |
| `confidence < 0.5` | Discard; fall back to the local embedding tier (§4.2.5); UI flags it |
| `goal_aligned` (Noul) | Only meaningful when a `distraction` category is contested: `distraction` + `goalAligned ≥ 0.6` → downgrade to `neutral`. `productive` never gets upgraded by Noul (no "double-forgiveness") |

Thresholds are config sliders; defaults above are starting points, retuned during M2.

#### 4.2.4 Cache, hygiene, and cost

- **Cache key (browser):** `domain + hash(pageTitle)`, *not* domain alone. Rationale: on YouTube, Reddit, docs, etc. the discriminating signal is the page title/URL **path**, while the query string (`?v=…`) must be stripped for privacy — so domain-level caching would let one tutorial verdict silently govern every other page on the site. App surfaces keep `(app, normalized title)`.
- **Title churn guard:** a media platform can change title without a decision-relevant change (playlist autoplay); significant request budget would burn on trivia.

  Coalescing cap: if a domain has had ≥ 5 requests in the trailing hour, new title hashes on that domain reuse the freshest cached answer for that *domain* (label only, marked `stale` in the UI) until the hour rolls off. Worst case stays bounded: ~5 deep classifications per domain per hour.
- **Deprecation of stale domain-level fallback:** a `stale` answer can never trigger escalation; it only holds soft-nudge authority at half the normal dwell (§4.4 note).
- **Cache content:** `key → {category, source, confidence, ts}`. Default TTL 7 days; user-editable; "clear cache" button.
- **Request budget:** at most 1 request per novel title (not per tick, not per scroll — the dwell debounce absorbs churn). Typical demo day: a few dozen requests.
- **Retries:** SDK `RetryPolicy` (2 attempts, capped backoff). On total failure the engine flips to the local tier (`source: "local"`) and shows it in the status bar, no crash, no stall.
- **Re-classification triggers:** goal changes invalidate only *contested* keys (the `distraction` ones are immediately re-asked; the obvious ones keep their cache).

#### 4.2.5 The local tier (embedding similarity against the goal)

A small sentence-embedding model runs on the CPU, entirely on the machine. It replaces the old hardcoded domain/app lists, so the offline path still judges surfaces **against the goal** rather than against a dictionary.

- **Model:** a MiniLM-class ONNX sentence encoder (all-MiniLM-L6-v2 or similar, ~90 MB) via `onnxruntime`. Loaded once at boot; one embedding is ~10 ms.
- **Input text:** `"{app or domain} {page_title or window_title}"`, the same fields Jev sees, after the never-send filter (§4.2.6) so excluded surfaces are never embedded either.
- **Scoring:** embed the goal, the surface, and a small fixed set of **distraction anchor phrases** ("watching entertainment videos", "scrolling social media", "playing games", "online shopping", "celebrity gossip"). With `g` = cosine(surface, goal) and `d` = max cosine(surface, anchors):
  - `g ≥ g_hi` → `productive`;
  - else `d - g ≥ margin` → `distraction`;
  - else → `neutral`.
  Relatedness to the goal can say "this is on topic", but low similarity alone does **not** mean distraction (mail, calendar, and file managers are unrelated to most goals and are neutral). That is why the anchors exist. `g_hi` and `margin` are config values, tuned against the recorded-surface fixture set (§9) rather than guessed.
- **Confidence:** `confidence` is derived from the score gap (`|d - g|` and distance from `g_hi`), clipped to 0..1, and is reported honestly in the UI as `local`. It is never presented as Jev-grade.
- **Uses:** (1) Jev unreachable / API error / timeout / `confidence < 0.5`; (2) the first seconds before Jev answers a novel surface, so the status strip is not blank. While Jev's answer is pending the local label is provisional and **cannot fire a nudge**. Once Jev is confirmed unreachable (request timeout or retries exhausted), local `distraction` verdicts may nudge, with the dwell doubled as for mid-confidence Jev answers.
- **Always dominated:** any cached or fresh Jev answer overrules the local tier; user overrides (§4.4) overrule everything.
- **Known weakness:** weaker than Jev on same-domain cases (§8 #3b). Acceptance for the local tier is looser (see §8 #8).

**Startup sequence:** load the embedding model and cache on boot; re-ask nothing. Show local-tier labels flagged `local` until the user's first novel surfaces are answered by Jev.

#### 4.2.6 Never-send surfaces

Some surfaces must not leave the machine, not even as a title. A surface matching any of these is **not sent to Jev and not embedded**; it is treated as `neutral`, shown in the UI as `private`, and excluded from PI:

- Incognito / InPrivate windows (the extension reports `incognito: true`; the extension is only allowed in incognito if the user enables it, otherwise those tabs are simply invisible to it and the app watcher sees a generic browser window with the title withheld);
- Password managers and banking: a small default list of apps and domains, user-editable;
- Any surface the user marks with the **"exclude this surface"** dashboard button (stored in `config.json` as an app or domain pattern).

### 4.3 Vision (engine, precise head pose)

- **Stack:** OpenCV `VideoCapture` (DirectShow), MediaPipe Face Landmarker Python, 478 landmarks; capture 30 FPS, inference throttled to 5–10 FPS with frame-skip.
- **Presence:** confidence > 0.5; confirm after 2 consecutive hits; clear after 5 consecutive misses.
- **Facing (degree-based):** 2D landmarks matched to the canonical 3D face model → `cv2.solvePnP` → Euler yaw/pitch. `facing = |yaw| < 20° AND pitch ∈ [−15°, +20°]` (slider-tunable). Nose-tip-in-box check survives as a cheap fallback while PnP warms up.
- **Perf & hygiene:** ≤ 8% sustained CPU budget across engine threads; frames dereferenced after inference; **zero disk writes, zero network sends of pixels** — asserted by a no-`imwrite`/no-`fetch` audit for the vision thread, and "Jev never receives images" is a design invariant verified in review.
- **Camera lifecycle:** user toggle in dashboard; camera releases on `afk` and on system sleep; LED turning off during `afk` is an acceptance criterion.
- **Graceful degradation:** no camera / permission denied → input-only mode with `degraded` camera panel. Flap guard: > 5 `present` toggles/min → `degraded` for 10 min, `facing = unknown` (no false nudges in bad lighting).

### 4.4 Decision engine (state machine)

Runs every 2 s; inputs: `{surface.kind, surface.label, category, categoryPending, facing, present, inputIdleSec, audible}`.

```
focused ──────► distracted ──────► nudged ─┐
   │  ▲            ▲     │               │ (snooze / reclassify)
   │  └────────────┘     └──────────────►┘
   ▼
  afk (from any state, when inputIdleSec > 180 AND !present)
   ▲
quiet_thinking (distracting surface + !facing + present)
```

| Inputs | Result |
|---|---|
| Productive surface + facing + present | `focused` — nudges suppressed, PI accrues |
| Distraction surface + facing + present, sustained 60 s (×2 if Jev confidence ∈ [0.5, 0.8)) | `distracted` → soft nudge, once per session per surface |
| Distraction surface + NOT facing + present | `quiet_thinking` — no nudge; resets dwell |
| `category = unknown` and classification pending | **No nudge can fire**; surface counted as neutral in PI until resolved |
| Any → `inputIdleSec > 180` and face absent | `afk` — clock pauses, camera releases |
| Distraction → productive switch within 5 s while facing | Immediate soft nudge, max once per 10 min |
| `!cameraOn` or degraded | `facing = unknown`; input-idle arithmetic only |
| Audible media + browser not foreground | treat as neutral (background music), no nudge |

**Anti-annoyance:** 1 nudge per surface per 5 min · max 3/hour globally · snooze = 10 min trust · user reclassification retracts instantly and persists as an override that beats Jev, rules, and cache.

**PI accounting:** `focused` +1 · `distracted` −1 · `afk`/`quiet_thinking`/`unknown-pending` 0. Window: last 60 min.

### 4.5 UX surfaces

1. **Dashboard** (`localhost:8765`): status strip `[State: Focused] [Goal: write spec] [Camera: Present · yaw −4°] [Surface: github.com (browser · Jev 0.91)]`, PI sparkline, **goal editor (one text box, first control on the page)**, cache inspector (see every Jev answer + confidence + source), sliders, demo buttons.
2. **Windows toast** on nudge escalation only.
3. **Overlay strip / tray icon** (stretch).
4. **Honesty label:** the status bar always shows `categorySource` — the user is never left wondering whether a robot or a rule made a call.

No modal dialogs, no sounds, no animations > 200 ms.

### 4.6 Demo & instrumentation

- **`D` hotkey / button:** replays a canned 20-min distraction scenario through the real pipeline (dwell → soft nudge → escalation), zero camera needed.
- **`G` scenario button:** types a fresh goal ("editing the demo reel"), opens an unknown entertainment site, and shows classification arriving in ~1 s with the confidence label — the semantic-layer showcase.
- **`R` button:** reset PI and clock.
- **Cache pre-warm script:** before a live demo, runs the demo-day surface list through Jev once so answers arrive from cache instantly.

---

## 5. Data & Storage

| Store | Content |
|---|---|
| In-memory | Vision events (last 30), tick state, PI window, surface history (rolling 1 h) |
| `classifications.json` (persisted, user dir) | classification cache: key → `{category, source, confidence, ts}` |
| `config.json` | goal history (last 10), thresholds, confidence routing values, overrides, snoozes, demo script |
| **Sent to Jev** | goal text, surface `{kind, app, domain, url-path (query & fragment stripped, depth-capped), pageTitle, windowTitle}` — and nothing else |
| **Never sent to Jev** | Anything matching the §4.2.6 never-send list (incognito windows, password managers, banking, user-excluded surfaces). Those titles are not embedded locally either |
| **Never sent anywhere / never stored** | query strings & URL fragments (can embed tokens/PII), cookies, credentials, camera frames, screenshots, face embeddings, keystroke content, page content, audio |

WS binds to `127.0.0.1`. Extension keeps its own settings in `storage.sync` (engine URL only). All timestamps epoch ms UTC.

**API key handling:** `TYPESAFE_API_KEY` from an environment variable or `.env` (gitignored); the value never enters `config.json` or logs.

---

## 6. Milestones

**M1 — Engine skeleton walks (browser-only demo equivalence)**
1. Python scaffold (`uv` + `ruff`), WS server, static dashboard, config loader.
2. Extension skeleton: background MV3 → `BrowserEvent` over WS with reconnect/backoff.
3. App watcher thread + `GetLastInputInfo` + dwell debounce; fused tick → dashboard status strip (category `unknown` everywhere, no classification yet — proves plumbing).
4. **Classifier spike:** one hand-written script asks Jev to classify 5 sample surfaces; validates SDK, key, latency, output shape (this de-risks the core assumption on day one, before anything else is polished).

**M2 — Classification + vision (the two signal layers)**
5. Classifier component: async requests, Choice + Noul batching, confidence routing, persistent cache, rules fallback, retry/offline handling.
6. Session goal: dashboard editor, goal history, goal-change cache-invalidation for contested keys.
7. Vision: MediaPipe presence + `solvePnP` yaw/pitch + flap suppression; fused with classification into the state machine; anti-annoyance caps; PI chart live.

**M3 — Polish & demo**
8. Cache inspector UI (show every answer the model gave and which source was trusted), settings sliders, toast escalation.
9. `D` / `R` / `G` demo controls, cache pre-warm script, README + 60-second demo script.
10. Fallback drills tested: Jev down / camera denied / extension offline / no internet.

**M4 — Stretch**
- Tray icon with state glyph; always-on-top overlay strip.
- SQLite session history + CSV export.
- Goal-conditioned Score primitive: rate every surface's goal-alignment 0–1 for richer PI weighting (needs calibration tuning across real goals).

---

## 7. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| **Jev latency or outage stalls nudges** | classification is async; the state machine counts `unknown-pending` as neutral (no false nudges, no hang). API failure → local embedding tier; the demo degrades but never breaks |
| Demo laptop lacks internet | `D` demo key + local embedding tier + cache pre-warm means the entire demo can run offline; M3 tests the no-internet drill explicitly |
| API key leaks into the repo | env var only, gitignored `.env`, pre-commit grep for the key pattern; never logged (SDK supports level control) |
| Model cost runs hot on a long demo | 1 request per novel surface, persistent cache, ≤ 600-char state; realistic ceiling a few dozen calls per day |
| Jev mislabels (jaggedness, adversarial titles like "smoothie recipes.py") | confidence routing (low-confidence answers don't act), Companion-Noul pattern for contested categories, user override panel always visible; [model-jaggedness.md](https://docs.typesafe.ai/model-jaggedness.md) tracked for known edge cases |
| Privacy optics ("you're sending my screen names to the cloud") | status bar always surfaces `categorySource` and a one-line "what we send" explainer link; camera frames provably never leave the machine (§8 audit); camera is one-click off |
| `solvePnP` yaw/pitch unstable in low light | slider thresholds, nose-box fallback, flap guard downgrades to `unknown` |
| Chrome-title ↔ extension-domain mispairing | pair by 5 s recency; cross-check foreground process |
| Extension drops (lid close, engine restart) | exponential backoff; engine keeps whole-screen tracking with `degraded: browser-offline` |

---

## 8. Acceptance Criteria (v1)

1. With goal "write the spec": on `github.com` facing → `focused`, PI rises, no nudges in 10 min.
2. **Goal conditioning:** with goal "editing the demo reel", a demo-video surface on a neutral-entertainment site classifies `productive` (cached + confidence shown); with the goal switched to "write the spec", the same key reclassifies within the session.
3. A surface the rules tier has never seen (e.g. `omegle.com` — deliberately absent from lists) lands `distraction` with `source: "jev"` and confidence ≥ 0.8 within ~5 s of the tab change.
3b. **Same-domain discrimination (title does the work):** under goal "study for the physics exam", `youtube.com/watch` with page title "Capacitor charging basics — explained" classifies `productive`/`neutral`, while `youtube.com/watch` with page title "funny cats 10 hours" classifies `distraction` — distinct cache keys, two answers cached, extension-sent URL paths and titles visible in the cache inspector.
4. Sit on a distraction-classified surface facing the screen for 60 s: soft nudge appears once; dwell doubles when confidence ∈ [0.5, 0.8); escalation after 2 min if continued.
4b. **Autoplay churn bounded:** binging 20 pages within one hour on a single domain triggers ≤ 5 Jev requests per hour + up to 1 per new hour after that (coalescing cap holds, per-domain cooldown verified in logs).
5. Turn away + `inputIdleSec > 3 min`: `afk`, clock freezes, camera releases (LED off).
6. Distraction surface + turn away while present: no nudge (`quiet_thinking` in the log).
7. Camera denied: fully functional input-only mode with `degraded` panel.
8. Kill the network mid-demo: engine switches to `source: "local"` labels, remains responsive, no hangs, demo scenario still runs. The local tier must get the demo-surface fixture set right (clearly productive and clearly distracting surfaces); borderline same-domain cases may degrade to `neutral`.
8b. **No goal:** with the goal cleared, the engine makes no Jev calls, fires no nudges, and PI does not move; the dashboard prompts for a goal.
8c. **Never-send:** an incognito window and a surface marked "exclude this surface" produce zero outbound requests (verified in the same outbound-HTTP recording as #10) and show as `private`.
9. `D` hotkey: full distraction scenario runs end-to-end with zero camera.
10. **Privacy audit:** zero image bytes written or transmitted during a full demo session; the only outbound payloads are the §5 "Sent to Jev" set, verified by recording all outbound HTTP.
11. Sustained engine CPU ≤ 8%; dashboard state render < 100 ms.

---

## 9. Tech Stack

- **Engine:** Python 3.12 · **`typesafe-sdk`** (Jev client, model `jev-latest`) · FastAPI for the extension WS only · **PySide6** native window + tray (replaces the web dashboard) · **`onnxruntime` + a MiniLM-class sentence encoder** (local tier, §4.2.5) · **pywin32** on Windows / **python-xlib** on Linux X11 (observer only, no admin/root) · **OpenCV** capture · **MediaPipe Tasks Python** Face Landmarker (CPU delegate default) · `keyboard` (global hotkeys) · `plyer`/PowerShell toasts · `logging` text-only rotating log.
- **Extension:** TypeScript, plain MV3 (background + manifest only), no build framework.
- **Dashboard:** vanilla HTML/JS + canvas chart, framework-free.
- **Testing:** `pytest` for the state machine, confidence-routing table, cache behavior, and local embedding tier (synthetic events); a recorded-surface fixture suite replaying past Jev answers so classifier behavior is testable offline; vision smoke-tests against a webcam fixture; M1.4 spike script doubles as a live integration test.

---

## 10. Open Questions

1. Confidence thresholds (0.8 act / 0.5 fall back) — sensible starting points or misfits? Retune during M2 with real usage.
2. Should Noul `goal_aligned` also be able to *escalate* a `neutral` label toward `distraction`, or only forgive (current design)? Leaning: forgive-only during v1.
3. Cache granularity vs. cost trade-off: browser surfaces now key on `domain + page-title hash` with a 5/hour/domain coalescing cap — optimize during M2 once real browsing patterns are observed (a playlist-heavy day may want a smarter title-diff rule).
4. Do we want the Classification's `Score` primitive on the PI (weight ticks by goal-alignment 0–1) or keep PI state-based (M4 candidate either way)?
5. Product name (placeholder: **Focus Tracker**).

**Raised in the 0.4 design review.** Decided 2026-11-06: #6, #8, #9 and #10 are adopted as proposed and override the text above; #7 is still open (§4.4 text stands).

6. **State-table typo (§4.4):** "Distraction → productive switch within 5 s while facing → immediate soft nudge" nudges the user for returning to work. Proposed: make it "reset dwell, no nudge".
7. **One nudge ladder:** §4.4 says "once per session per surface", the anti-annoyance line says "1 per surface per 5 min", and §8 #4 mentions an escalation step the state machine lacks. Proposed: soft nudge at the dwell threshold, toast at +2 min if still distracted, 10-minute cooldown per surface, global cap 3/hour.
8. **Cache key ignores the goal (§4.2.4):** `domain + hash(pageTitle)` would serve a `productive` verdict from goal A after switching to goal B. Proposed: include a goal hash in the key and drop the "contested keys only" invalidation.
9. **Is the Noul `goal_aligned` question needed?** The Choice question already receives the goal. Proposed: drop it from v1 (this also resolves open question 2).
10. **Facing calibration (§4.3):** the fixed yaw/pitch box assumes a camera centered above the screen. Proposed: a 3-second "look at the screen" calibration that stores baseline yaw/pitch and applies the thresholds as offsets.
