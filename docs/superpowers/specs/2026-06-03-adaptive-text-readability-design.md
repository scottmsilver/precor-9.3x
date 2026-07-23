# Adaptive Text Readability on Background Images — Design

> **Status update (2026-07-23):** the web UI was removed from the repo; the TS twin (`web/src/bglab/`) and the `/bg-lab` tuning bench were retired with it. The Kotlin engine (`kotlin/.../ui/theme/readability/`) plus `docs/bg-lab/golden.json` (pinned by `GoldenSyncTest`) are now the single authoritative implementation and spec vectors.

**Date:** 2026-06-03
**Status:** Approved design, pending implementation plan
**Surfaces:** Android Running screen (runtime), web `/bg-lab` (tuning preview), server.py (Gemini advisor)

## Problem

The Android Running screen (`kotlin/.../ui/screens/running/RunningScreen.kt`) draws a
full-bleed photographic background (`bg_forest`) with glass panels and free-floating text
(hero timer, metric tiles, button row) on top. Readability today is handled by
`GlassTheme.kt` / `rememberGlassParams`, which:

- samples **one global average brightness** over the whole image (200×125 downscale, Rec.601 luma),
- maps that single scalar to blur / black-panel opacity / white-border opacity / black scrim opacity,
- **always tints panels black and borders white** — no photo-derived color, no per-region awareness,
- has **no contrast metric** verifying the text is actually legible.

Consequences: a panel sitting over a bright clearing can fall below a legible contrast while
the global average looks "medium"; the look is monochrome-black regardless of the photo; and the
whole thing is hand-tuned with no way to see the tradeoff.

Backgrounds will be **dynamic and unknown** at runtime (route scenery, generated images, user
choices), so the system must earn legibility on photos it has never seen.

## Goals

1. Pick overlay/scrim **colors derived from the photo**, not always black.
2. Drive decisions with a **mathematical legibility metric** (APCA) — a hard guarantee, not a heuristic.
3. **Hold a beauty standard** — express "beautiful" as a cost we minimize among legible options.
4. Keep the **UI consistent** — one coherent look across the whole screen, not a patchwork per panel.
5. Provide a **web preview / tuning tool** to dial in the taste knobs and see the math live.

## Non-Goals

- Drop-shadows / glows on free-floating text (deliberately excluded — the timer earns legibility
  from scrim + blur + color choice, not a glow crutch).
- Per-frame network calls. Gemini is a cached, per-image advisor only.
- Shipping the web tuning tool to the treadmill UI — it is a developer bench tool.

## Decisions (from brainstorming)

| Question | Decision |
|---|---|
| Where do the smarts live? | **Runtime feature on Android.** Web preview is a tuning/visualization aid. |
| How do backgrounds vary? | **Dynamic / unknown images** — must handle arbitrary photos at runtime. |
| Contrast metric | **APCA** (Lc), polarity-aware. WCAG ratio optionally shown in preview only. |
| Vision model role | **Gemini = cached advisor (prior), never authority.** APCA on-device = the guarantee. |
| Allowed levers | Photo-derived **colored scrim/tint**, **text color flip (ivory↔charcoal)**, **adaptive blur + gradient scrim**. (No shadows/halos.) |
| Consistency | **Two-tier:** one screen-level Theme (global identity) + local scrim-alpha-only adjustment. |

## Architecture

Three units, each independently testable, talking through narrow interfaces.

### 1. Sampling + treatment engine (Kotlin, on-device)

Replaces the scalar-brightness logic in `GlassTheme.kt`. Pure, side-effect-free functions.

- `sampleRegion(bitmap, rect) → RegionStats` — local luminance histogram + dominant/average color
  under a given text rect (not the whole image).
- `harmonizePalette(globalStats, prior) → List<TintCandidate>` — photo-cohesive tint colors
  (dominant darkened, complementary-muted, neutral fallback), biased toward `prior.palette_hue`.
- `apcaLc(textColor, bgColor) → Float` — APCA contrast number; sign encodes polarity.
- `beautyCost(theme) → Float` — penalizes heavy scrim / opacity / blur and off-palette tint.
- `chooseTheme(globalStats, prior, targets) → Theme` — global identity (see Two-Tier Model).
- `fitRegion(theme, regionStats) → scrimAlpha` — local scrim-only adjustment within the fixed theme.

Types:

```
RegionStats   = { lumaHistogram, avgColor, dominantColor }
TintCandidate = { color, paletteDistance }
Theme         = { tintColor, textColor, blurDp, baseScrimAlpha }
```

`glassPanel` / `glassPanelTinted` consume a resolved per-region treatment
(`Theme` + `fitRegion` scrim alpha) instead of today's `GlassParams`.

### 2. Gemini advisor (server.py + program_engine.py)

- **Endpoint:** `POST /api/background/advise`, body `{ image_hash, image_b64? }`.
  Cache hit by hash → return immediately; miss → one Gemini call, cache to `background_advice.json`
  (rolling-JSON pattern like `program_history.json`), return.
- **Returns `AdvicePrior`** (every field optional — Postel's Law):

```json
{
  "palette_hue": 158,
  "suggested_polarity": "light",
  "mood": "cool-forest",
  "busy_zones": [ { "x":0.0, "y":0.55, "w":1.0, "h":0.45, "note":"foliage detail" } ]
}
```

- **Used strictly as a prior:** `palette_hue` seeds `harmonizePalette`; `suggested_polarity` sets
  which text color `chooseTheme` tries first; `busy_zones` raise the per-region scrim baseline where
  the photo is visually noisy. Every prior field can be overruled by APCA + beautyCost.
- **Failure behavior:** no server / no key / timeout / garbage JSON → neutral prior (desaturated hue,
  light polarity, no busy zones). APCA still guarantees legibility; only the photo-matched hue bias is lost.
- **Prompt** lives in `program_engine.py` (versioned, testable), requests exactly the schema above via
  the SDK's structured output.

### 3. Web preview / tuning tool (TypeScript)

- **Route:** dev-only `/bg-lab` in `web/` (not in treadmill nav). Open via the Caddy URL.
- **TS port of the same pure functions** in `web/src/bglab/engine.ts`, kept identical to Kotlin by a
  shared golden-vector file (see Testing).
- **Capabilities:** load a photo (file picker or bundled set) drawn full-bleed; mock the real layout
  (hero timer, metric tiles, button row) at true relative positions so regions sample the same places;
  live per-block **APCA Lc** with pass/fail vs role target; render the chosen `Theme` plus runner-up
  candidates with their beauty-cost and why they lost; sliders for role Lc targets and beautyCost
  weights; toggles to force polarity / disable a lever; "call Gemini advisor" to fetch a real
  `AdvicePrior` (and toggle it off to see the neutral path); **export** tuned constants as a snippet.

## Two-Tier Model (consistency guarantee)

Running `chooseTheme` independently per region would produce a patchwork (different hue / text color
per panel). Instead:

- **Tier 1 — one screen-level `Theme`, decided once per background.** From the *global* sample + prior,
  pick a single tint **hue**, text **color** (the ivory↔charcoal flip happens here, once, for the whole
  screen), blur radius, and base scrim. Every panel and text block shares these. Consistency anchor.
- **Tier 2 — per region, only scrim opacity of the same tint flexes.** A region over a bright area gets
  a stronger scrim of the *identical* hue to hit its APCA target; hue and text color never change between
  regions.
- **If max scrim can't rescue a region** at the chosen global text color, the engine does **not** flip
  that one region. It raises the **global baseline** (bump whole-screen scrim, or flip the global text
  color) and re-evaluates everything together. The screen always converges to one coherent treatment.

## Math Core

### APCA

Implement the APCA-W3 Lc formula (standard 0.98G constants): linearize sRGB → screen luminance Y with
the APCA exponents, apply the soft-black clamp, take the polarity-correct delta (light-on-dark vs
dark-on-light use different forms), scale to Lc. One pure function; sign tells polarity. Public,
well-specified math — identical constants ported to Kotlin and TS.

### Targets (the legibility contract), by role

| Role | Target |
|---|---|
| Hero timer | **Lc ≥ 75** |
| Body metrics / labels | **Lc ≥ 60** |
| Secondary / muted text | **Lc ≥ 45** |

One constants table, surfaced as preview sliders, then baked.

### `beautyCost(theme)` — "hold a beauty standard," numerically

A weighted sum minimized among legible candidates:

- **scrim opacity** — heaviest weight (muddy panel-over-photo is the main beauty loss).
- **blur radius** — medium (frosted is nice but costs photo detail).
- **tint distance from harmonized palette** in OKLab ΔE — medium (penalize off-palette tints).
- **charcoal-text-on-dark-mood penalty** — small (keep the warm/light default unless the photo demands the flip).

### `chooseTheme` algorithm

Candidate set = {tint ∈ harmonized palette} × {text ∈ ivory, charcoal} × {scrim base ∈ stepped levels}
× {blur ∈ stepped levels}. For each candidate, check that *every* region can reach its role's target Lc
using only the local scrim-alpha lever within a clamp. Keep candidates where all regions pass; return the
global min-`beautyCost` survivor. If none pass (pathological photo), return the maximum-scrim neutral
fallback — never illegible, just less pretty. Deterministic; fixed-size candidate array, no hot-path
allocation.

The beautyCost weights and the palette-harmonization rule are the tunable "taste" knobs — the reason the
web preview exists.

## Testing

### Golden vectors (anti-drift spine)

`docs/bg-lab/golden.json`: hand-checked `(textColor, bgColor) → expected Lc` cases (from the APCA
reference set, to verify our constants), plus full `(globalStats, prior, targets) → Theme` cases. Both
the Kotlin engine test and the TS engine test read this same file and assert against it. Divergence
between the two implementations fails a test.

### Kotlin (engine unit tests, plain JVM)

`apcaLc` (golden); `harmonizePalette` (stays near prior hue, always includes neutral fallback);
`beautyCost` (monotonic in scrim/blur); `chooseTheme` (every returned Theme passes all role targets;
all-midtone pathological photo still returns the legible fallback); `fitRegion` (scrim clamps, never
exceeds max).

### TS (preview engine tests, Vitest)

Same golden file. Plus a render smoke test that `/bg-lab` mounts and computes Lc for a known photo.

### Python (advisor tests)

`test_background_advice.py`: cache hit returns without calling Gemini; malformed/partial model JSON
coerced to a valid `AdvicePrior` (missing fields → neutral defaults); Gemini-unreachable → neutral
prior; endpoint validates `image_hash`. Mocked Gemini for unit tier; one live test (skipped without API
key) that a real call returns the schema.

### Regression (would-fail-without-the-change)

A test asserting a **known-bright** photo region yields text that clears its APCA target — which the
current global-average `GlassParams` does **not** guarantee. Fails on `main`, passes with the engine.

## Security (per audit protocol)

- The advisor endpoint accepts an image over HTTP: bound image size, validate it decodes, never shell
  out, never log image bytes.
- Run the two-track audit before declaring done: `pip-audit` on any new Python deps + a `codex exec
  --sandbox read-only` review of the new endpoint and engine, with concrete line numbers.

## Dual-platform note

Per project rules, UI changes land in both web and Kotlin. Here the **runtime feature is Android-only**
(it's where the photo background lives); the **web side is the tuning tool**, not a shipped UI surface.
The web app does not currently use a photographic background, so there is no shipped web Running surface
to update. If a photographic background is added to the web Running screen later, the already-ported TS
engine drops straight in.

## Open implementation questions (for the plan)

- Exact stepped levels for scrim base and blur (start coarse, refine in the lab).
- How regions are registered (explicit rect list vs a layout pass that reports text bounds).
- Whether `harmonizePalette` works in OKLCH end-to-end or converts per-candidate.
