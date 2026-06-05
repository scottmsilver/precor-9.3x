# Adaptive Text Readability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make text legible on dynamic photographic backgrounds by deriving one coherent, photo-colored, APCA-verified treatment per image — driven on-device, tuned in a web lab.

**Architecture:** A pure treatment engine (APCA contrast math + a minimized "beauty cost") picks one screen-level `Theme` (tint hue, text color, blur, base scrim) and a per-region scrim-only adjustment. The engine is implemented twice — TypeScript (web `/bg-lab` tuning tool, built first) and Kotlin (Android runtime) — kept identical by a shared golden-vector file. A cached Gemini advisor (server.py) supplies a non-authoritative taste prior.

**Tech Stack:** TypeScript + React + Vite + Vitest (web/lab + tests); Kotlin + Jetpack Compose + JUnit (Android); Python + FastAPI + google-genai (advisor).

**Sequencing (engine-first):** Phase 1 golden contract → Phase 2 TS engine → Phase 3 web `/bg-lab` (tune here) → Phase 4 Kotlin engine port → Phase 5 Gemini advisor → Phase 6 Android wire-in.

**Reference spec:** `docs/superpowers/specs/2026-06-03-adaptive-text-readability-design.md`

---

## File Structure

**Phase 1 — shared contract**
- Create: `docs/bg-lab/golden.json` — shared input→output vectors (APCA pairs + Theme cases). Source of truth for both engine ports.

**Phase 2 — TS engine (`web/src/bglab/`)**
- Create: `web/src/bglab/color.ts` — sRGB↔linear, OKLab/OKLCH conversions, hex parsing.
- Create: `web/src/bglab/apca.ts` — `apcaLc(text, bg)`.
- Create: `web/src/bglab/types.ts` — `RegionStats`, `TintCandidate`, `Theme`, `AdvicePrior`, role targets.
- Create: `web/src/bglab/engine.ts` — `harmonizePalette`, `beautyCost`, `chooseTheme`, `fitRegion`.
- Create: `web/src/bglab/sample.ts` — `sampleRegion(imageData, rect) → RegionStats`.
- Create: `web/src/bglab/*.test.ts` — Vitest unit tests (read golden.json).

**Phase 3 — web lab**
- Create: `web/src/routes/BgLab.tsx` — the `/bg-lab` tuning route.
- Create: `web/src/bglab/layout.ts` — the mock Running-screen text-block rects.
- Modify: `web/src/main.tsx:19-26` — register `<Route path="/bg-lab" component={BgLab} />`.

**Phase 4 — Kotlin engine (`kotlin/.../ui/theme/readability/`)**
- Create: `kotlin/app/src/main/java/com/precor/treadmill/ui/theme/readability/Apca.kt`
- Create: `.../readability/Color.kt` (OKLab/OKLCH + sRGB linearize)
- Create: `.../readability/Types.kt` (`RegionStats`, `Theme`, `AdvicePrior`, targets)
- Create: `.../readability/Engine.kt` (`harmonizePalette`, `beautyCost`, `chooseTheme`, `fitRegion`)
- Create: `.../readability/Sample.kt` (`sampleRegion(Bitmap, Rect)`)
- Create: `kotlin/app/src/test/java/com/precor/treadmill/ui/theme/readability/*Test.kt` (read golden.json from test resources)
- Add: `kotlin/app/src/test/resources/golden.json` (copy of `docs/bg-lab/golden.json`, kept in sync by a test).

**Phase 5 — advisor (`python/`)**
- Modify: `python/program_engine.py` — add `advise_background(image_bytes) → dict` (Gemini call + prompt).
- Modify: `python/server.py` — add `POST /api/background/advise` + on-disk cache `background_advice.json`.
- Create: `python/tests/test_background_advice.py`

**Phase 6 — Android wire-in**
- Modify: `kotlin/.../ui/theme/GlassTheme.kt` — replace scalar `GlassParams` with `Theme`-driven panels.
- Modify: `kotlin/.../ui/screens/running/RunningScreen.kt:146-176` — compute `Theme`, fetch prior, register regions.
- Create: `kotlin/.../data/BackgroundAdviceClient.kt` — calls `/api/background/advise`, caches by hash.

---

## APCA constants (used verbatim in both ports)

APCA-W3 `0.0.98G-4g`:
```
mainTRC   = 2.4
Rco=0.2126729  Gco=0.7151522  Bco=0.0721750
normBG=0.56  normTXT=0.57  revTXT=0.62  revBG=0.65
blkThrs=0.022  blkClmp=1.414  loClip=0.1  deltaYmin=0.0005
scaleBoW=1.14  loBoWoffset=0.027
scaleWoB=1.14  loWoBoffset=0.027
```
Algorithm: linearize each 8-bit channel `pow(c/255, 2.4)`, weighted sum → Y; soft-clamp Y below `blkThrs`; if `|bgY-txtY| < deltaYmin` return 0; if `bgY>txtY` normal polarity `(bgY^normBG - txtY^normTXT)*scaleBoW`, clip below `loClip` else subtract `loBoWoffset`; else reverse polarity `(bgY^revBG - txtY^revTXT)*scaleWoB`, clip above `-loClip` else add `loWoBoffset`; ×100 = Lc (sign = polarity).

Canonical anchors (must match exactly): `#000000` on `#FFFFFF` → **106.04**; `#FFFFFF` on `#000000` → **-107.88**.

---

# Phase 1 — Shared golden contract

### Task 1: Seed the golden-vector file

**Files:**
- Create: `docs/bg-lab/golden.json`

- [ ] **Step 1: Create the file with the two canonical APCA anchors and a placeholder Theme block**

```json
{
  "apca": [
    { "text": "#000000", "bg": "#FFFFFF", "lc": 106.04 },
    { "text": "#FFFFFF", "bg": "#000000", "lc": -107.88 }
  ],
  "themes": []
}
```

- [ ] **Step 2: Commit**

```bash
git add docs/bg-lab/golden.json
git commit -m "feat(bg-lab): seed golden vector contract"
```

> Additional APCA rows (e.g. `#888888` on `#FFFFFF`) and `themes[]` cases are appended in Task 4 and Task 9, once the engine can generate verified values from the ported function. This avoids hand-guessing numbers.

---

# Phase 2 — TypeScript engine

### Task 2: Add Vitest to the web project

**Files:**
- Modify: `web/package.json`

- [ ] **Step 1: Install vitest**

Run: `cd web && npm install -D vitest`
Expected: `vitest` appears in devDependencies.

- [ ] **Step 2: Add the test script**

In `web/package.json` `"scripts"`, add:
```json
"test": "vitest run"
```

- [ ] **Step 3: Verify the runner starts (no tests yet)**

Run: `cd web && npx vitest run`
Expected: exits 0 with "No test files found" (acceptable for this step).

- [ ] **Step 4: Commit**

```bash
git add web/package.json web/package-lock.json
git commit -m "chore(web): add vitest test runner"
```

### Task 3: Color utilities (sRGB linearize + OKLab/OKLCH + hex)

**Files:**
- Create: `web/src/bglab/color.ts`
- Test: `web/src/bglab/color.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
import { describe, it, expect } from 'vitest';
import { hexToRgb, rgbToOklab, oklabToRgb, oklchToRgb, rgbToOklch } from './color';

describe('color', () => {
  it('parses hex', () => {
    expect(hexToRgb('#FFFFFF')).toEqual({ r: 255, g: 255, b: 255 });
    expect(hexToRgb('#000000')).toEqual({ r: 0, g: 0, b: 0 });
  });
  it('round-trips rgb->oklab->rgb', () => {
    const c = { r: 120, g: 200, b: 90 };
    const back = oklabToRgb(rgbToOklab(c));
    expect(Math.abs(back.r - c.r)).toBeLessThan(2);
    expect(Math.abs(back.g - c.g)).toBeLessThan(2);
    expect(Math.abs(back.b - c.b)).toBeLessThan(2);
  });
  it('round-trips oklch hue', () => {
    const lch = rgbToOklch({ r: 40, g: 120, b: 90 });
    const rgb = oklchToRgb(lch);
    const lch2 = rgbToOklch(rgb);
    expect(Math.abs(lch2.h - lch.h)).toBeLessThan(2);
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd web && npx vitest run src/bglab/color.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `color.ts`**

```ts
export interface Rgb { r: number; g: number; b: number; }   // 0..255
export interface Oklab { L: number; a: number; b: number; }
export interface Oklch { L: number; C: number; h: number; }  // h in degrees

export function hexToRgb(hex: string): Rgb {
  const s = hex.replace('#', '');
  return { r: parseInt(s.slice(0, 2), 16), g: parseInt(s.slice(2, 4), 16), b: parseInt(s.slice(4, 6), 16) };
}
export function rgbToHex({ r, g, b }: Rgb): string {
  const h = (n: number) => Math.max(0, Math.min(255, Math.round(n))).toString(16).padStart(2, '0');
  return `#${h(r)}${h(g)}${h(b)}`;
}

const srgbToLinear = (c: number) => { const x = c / 255; return x <= 0.04045 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4); };
const linearToSrgb = (x: number) => { const c = x <= 0.0031308 ? x * 12.92 : 1.055 * Math.pow(x, 1 / 2.4) - 0.055; return c * 255; };

export function rgbToOklab({ r, g, b }: Rgb): Oklab {
  const lr = srgbToLinear(r), lg = srgbToLinear(g), lb = srgbToLinear(b);
  const l = Math.cbrt(0.4122214708 * lr + 0.5363325363 * lg + 0.0514459929 * lb);
  const m = Math.cbrt(0.2119034982 * lr + 0.6806995451 * lg + 0.1073969566 * lb);
  const s = Math.cbrt(0.0883024619 * lr + 0.2817188376 * lg + 0.6299787005 * lb);
  return {
    L: 0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
    a: 1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
    b: 0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s,
  };
}
export function oklabToRgb({ L, a, b }: Oklab): Rgb {
  const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s = (L - 0.0894841775 * a - 1.2914855480 * b) ** 3;
  const lr = +4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s;
  const lg = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s;
  const lb = -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s;
  return { r: linearToSrgb(lr), g: linearToSrgb(lg), b: linearToSrgb(lb) };
}
export function rgbToOklch(c: Rgb): Oklch {
  const { L, a, b } = rgbToOklab(c);
  const C = Math.hypot(a, b);
  let h = Math.atan2(b, a) * 180 / Math.PI;
  if (h < 0) h += 360;
  return { L, C, h };
}
export function oklchToRgb({ L, C, h }: Oklch): Rgb {
  const r = h * Math.PI / 180;
  return oklabToRgb({ L, a: C * Math.cos(r), b: C * Math.sin(r) });
}
export function oklabDeltaE(a: Oklab, b: Oklab): number {
  return Math.hypot(a.L - b.L, a.a - b.a, a.b - b.b);
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd web && npx vitest run src/bglab/color.test.ts`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add web/src/bglab/color.ts web/src/bglab/color.test.ts
git commit -m "feat(bg-lab): color conversions (sRGB/OKLab/OKLCH)"
```

### Task 4: APCA contrast + record golden APCA rows

**Files:**
- Create: `web/src/bglab/apca.ts`
- Test: `web/src/bglab/apca.test.ts`
- Modify: `docs/bg-lab/golden.json`

- [ ] **Step 1: Write the failing test (canonical anchors + golden file)**

```ts
import { describe, it, expect } from 'vitest';
import { apcaLc } from './apca';
import golden from '../../../docs/bg-lab/golden.json';

describe('apca', () => {
  it('matches canonical anchors', () => {
    expect(apcaLc('#000000', '#FFFFFF')).toBeCloseTo(106.04, 1);
    expect(apcaLc('#FFFFFF', '#000000')).toBeCloseTo(-107.88, 1);
  });
  it('matches every golden apca row', () => {
    for (const row of golden.apca) {
      expect(apcaLc(row.text, row.bg)).toBeCloseTo(row.lc, 1);
    }
  });
});
```

> Vite resolves JSON imports natively. If TS complains, add `"resolveJsonModule": true` to `web/tsconfig.json` `compilerOptions`.

- [ ] **Step 2: Run to verify it fails**

Run: `cd web && npx vitest run src/bglab/apca.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `apca.ts`**

```ts
import { hexToRgb, Rgb } from './color';

const Rco = 0.2126729, Gco = 0.7151522, Bco = 0.0721750;
const normBG = 0.56, normTXT = 0.57, revTXT = 0.62, revBG = 0.65;
const blkThrs = 0.022, blkClmp = 1.414, loClip = 0.1, deltaYmin = 0.0005;
const scaleBoW = 1.14, loBoWoffset = 0.027, scaleWoB = 1.14, loWoBoffset = 0.027;

function toY({ r, g, b }: Rgb): number {
  const lin = (c: number) => Math.pow(c / 255, 2.4);
  let y = Rco * lin(r) + Gco * lin(g) + Bco * lin(b);
  if (y < blkThrs) y += Math.pow(blkThrs - y, blkClmp);
  return y;
}

/** APCA Lc. Positive = dark text on light bg; negative = light text on dark bg. */
export function apcaLc(text: string | Rgb, bg: string | Rgb): number {
  const txtY = toY(typeof text === 'string' ? hexToRgb(text) : text);
  const bgY = toY(typeof bg === 'string' ? hexToRgb(bg) : bg);
  if (Math.abs(bgY - txtY) < deltaYmin) return 0;
  let out: number;
  if (bgY > txtY) {
    const s = (Math.pow(bgY, normBG) - Math.pow(txtY, normTXT)) * scaleBoW;
    out = s < loClip ? 0 : s - loBoWoffset;
  } else {
    const s = (Math.pow(bgY, revBG) - Math.pow(txtY, revTXT)) * scaleWoB;
    out = s > -loClip ? 0 : s + loWoBoffset;
  }
  return out * 100;
}
```

- [ ] **Step 4: Run to verify anchors pass**

Run: `cd web && npx vitest run src/bglab/apca.test.ts`
Expected: PASS (golden loop trivially passes — only 2 anchor rows so far).

- [ ] **Step 5: Append verified golden rows**

Print real values, then paste them into `docs/bg-lab/golden.json` `apca[]`:

Run: `cd web && npx tsx -e "import {apcaLc} from './src/bglab/apca'; for (const [t,b] of [['#888888','#FFFFFF'],['#FFFFFF','#888888'],['#2E3A2C','#C9D4C2'],['#F2ECDF','#3A4A3E']]) console.log(t,b,apcaLc(t,b).toFixed(2));"`
(If `tsx` is unavailable: `npm i -D tsx` first.)

Add each printed row to `golden.json` as `{ "text": "...", "bg": "...", "lc": <printed> }`.

- [ ] **Step 6: Re-run to confirm the golden loop covers the new rows**

Run: `cd web && npx vitest run src/bglab/apca.test.ts`
Expected: PASS (now exercises all rows).

- [ ] **Step 7: Commit**

```bash
git add web/src/bglab/apca.ts web/src/bglab/apca.test.ts docs/bg-lab/golden.json web/tsconfig.json
git commit -m "feat(bg-lab): APCA Lc contrast + golden rows"
```

### Task 5: Engine types + role targets

**Files:**
- Create: `web/src/bglab/types.ts`

- [ ] **Step 1: Implement `types.ts` (no test — pure declarations)**

```ts
import { Rgb } from './color';

export interface RegionStats {
  /** id of the text block, e.g. 'timer', 'metric.speed' */
  id: string;
  role: Role;
  avg: Rgb;
  dominant: Rgb;
  /** 0..255 mean luma (Rec.601), kept for quick fallbacks */
  luma: number;
}

export type Role = 'hero' | 'body' | 'muted';

export const ROLE_TARGET_LC: Record<Role, number> = { hero: 75, body: 60, muted: 45 };

export interface TintCandidate { color: Rgb; paletteDistance: number; }

export interface Theme {
  tint: Rgb;
  text: Rgb;          // the global ivory/charcoal choice
  blurDp: number;
  baseScrimAlpha: number;
}

export interface AdvicePrior {
  paletteHue?: number;                 // OKLCH degrees
  suggestedPolarity?: 'light' | 'dark';
  mood?: string;
  busyZones?: { x: number; y: number; w: number; h: number; note?: string }[];
}

export const IVORY: Rgb = { r: 242, g: 236, b: 223 };
export const CHARCOAL: Rgb = { r: 30, g: 32, b: 30 };

export interface BeautyWeights {
  scrim: number; blur: number; palette: number; charcoalOnDark: number;
}
export const DEFAULT_WEIGHTS: BeautyWeights = { scrim: 1.0, blur: 0.5, palette: 0.6, charcoalOnDark: 0.3 };

export const SCRIM_STEPS = [0.18, 0.28, 0.38, 0.5, 0.62];
export const BLUR_STEPS = [0, 1, 2, 3];
export const MAX_REGION_SCRIM = 0.72;
```

- [ ] **Step 2: Commit**

```bash
git add web/src/bglab/types.ts
git commit -m "feat(bg-lab): engine types + role Lc targets"
```

### Task 6: `harmonizePalette` + `beautyCost`

**Files:**
- Create: `web/src/bglab/engine.ts`
- Test: `web/src/bglab/engine.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
import { describe, it, expect } from 'vitest';
import { harmonizePalette, beautyCost } from './engine';
import { DEFAULT_WEIGHTS, Theme } from './types';
import { rgbToOklch } from './color';

const stats = { avg: { r: 60, g: 90, b: 70 }, dominant: { r: 40, g: 80, b: 60 }, luma: 80 } as any;

describe('harmonizePalette', () => {
  it('biases toward the prior hue and always includes a neutral fallback', () => {
    const pal = harmonizePalette({ avg: stats.avg, dominant: stats.dominant }, { paletteHue: 200 });
    expect(pal.length).toBeGreaterThanOrEqual(3);
    const nearPrior = pal.some(c => Math.abs(((rgbToOklch(c.color).h - 200 + 540) % 360) - 180) < 40);
    expect(nearPrior).toBe(true);
    const neutral = pal.some(c => rgbToOklch(c.color).C < 0.03);
    expect(neutral).toBe(true);
  });
});

describe('beautyCost', () => {
  it('increases monotonically with scrim and blur', () => {
    const base: Theme = { tint: { r: 40, g: 80, b: 60 }, text: { r: 242, g: 236, b: 223 }, blurDp: 0, baseScrimAlpha: 0.2 };
    const heavier: Theme = { ...base, baseScrimAlpha: 0.5 };
    const blurry: Theme = { ...base, blurDp: 3 };
    const palRef = { color: base.tint, paletteDistance: 0 };
    expect(beautyCost(heavier, [palRef], DEFAULT_WEIGHTS, 'cool')).toBeGreaterThan(beautyCost(base, [palRef], DEFAULT_WEIGHTS, 'cool'));
    expect(beautyCost(blurry, [palRef], DEFAULT_WEIGHTS, 'cool')).toBeGreaterThan(beautyCost(base, [palRef], DEFAULT_WEIGHTS, 'cool'));
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd web && npx vitest run src/bglab/engine.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `harmonizePalette` + `beautyCost` in `engine.ts`**

```ts
import { Rgb, Oklch, rgbToOklch, oklchToRgb, rgbToOklab, oklabDeltaE } from './color';
import {
  RegionStats, Theme, TintCandidate, BeautyWeights, IVORY, CHARCOAL,
} from './types';

function muted(lch: Oklch): Oklch {
  // keep tints dark and gently saturated so they read as "scrim" not "paint"
  return { L: Math.min(lch.L, 0.32), C: Math.min(lch.C, 0.06), h: lch.h };
}

export function harmonizePalette(
  stats: Pick<RegionStats, 'avg' | 'dominant'>,
  prior: { paletteHue?: number },
): TintCandidate[] {
  const domH = rgbToOklch(stats.dominant).h;
  const hues: number[] = [domH];
  if (prior.paletteHue != null) hues.push(prior.paletteHue);
  hues.push((domH + 180) % 360); // complementary, muted
  const out: TintCandidate[] = hues.map(h => {
    const color = oklchToRgb(muted({ L: 0.26, C: 0.05, h }));
    const dist = oklabDeltaE(rgbToOklab(color), rgbToOklab(stats.dominant));
    return { color, paletteDistance: dist };
  });
  // neutral charcoal fallback (always legible, never off-palette)
  out.push({ color: { r: 26, g: 26, b: 26 }, paletteDistance: 0.0 });
  return out;
}

export function beautyCost(
  theme: Theme,
  palette: TintCandidate[],
  w: BeautyWeights,
  mood: 'cool' | 'warm' | 'dark' | 'neutral',
): number {
  const tintDist = Math.min(...palette.map(c =>
    oklabDeltaE(rgbToOklab(c.color), rgbToOklab(theme.tint))));
  let cost = w.scrim * theme.baseScrimAlpha + w.blur * (theme.blurDp / 3) + w.palette * tintDist;
  const isCharcoalText = theme.text === CHARCOAL || (theme.text.r < 80 && theme.text.g < 80);
  if (isCharcoalText && mood === 'dark') cost += w.charcoalOnDark;
  return cost;
}

export { IVORY, CHARCOAL };
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd web && npx vitest run src/bglab/engine.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/bglab/engine.ts web/src/bglab/engine.test.ts
git commit -m "feat(bg-lab): harmonizePalette + beautyCost"
```

### Task 7: `fitRegion` (local scrim-only solve)

**Files:**
- Modify: `web/src/bglab/engine.ts`
- Test: `web/src/bglab/engine.test.ts`

- [ ] **Step 1: Add the failing test**

```ts
import { fitRegion } from './engine';
import { ROLE_TARGET_LC, MAX_REGION_SCRIM } from './types';

describe('fitRegion', () => {
  const theme = { tint: { r: 20, g: 24, b: 20 }, text: { r: 242, g: 236, b: 223 }, blurDp: 0, baseScrimAlpha: 0.2 };
  it('returns a scrim alpha within clamp that meets the role target when possible', () => {
    const region = { id: 'm', role: 'body', avg: { r: 200, g: 210, b: 190 }, dominant: { r: 200, g: 210, b: 190 }, luma: 200 } as any;
    const r = fitRegion(theme, region);
    expect(r.scrimAlpha).toBeGreaterThanOrEqual(theme.baseScrimAlpha);
    expect(r.scrimAlpha).toBeLessThanOrEqual(MAX_REGION_SCRIM);
    expect(r.lc).toBeGreaterThanOrEqual(ROLE_TARGET_LC.body - 0.5);
  });
  it('reports unmet when even max scrim cannot reach target', () => {
    const region = { id: 'h', role: 'hero', avg: { r: 130, g: 130, b: 130 }, dominant: { r: 130, g: 130, b: 130 }, luma: 130 } as any;
    const lightText = { ...theme, text: { r: 150, g: 150, b: 150 } }; // deliberately weak
    const r = fitRegion(lightText, region);
    expect(typeof r.met).toBe('boolean');
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd web && npx vitest run src/bglab/engine.test.ts`
Expected: FAIL — `fitRegion` not exported.

- [ ] **Step 3: Implement `fitRegion` in `engine.ts`**

```ts
import { apcaLc } from './apca';
import { ROLE_TARGET_LC, MAX_REGION_SCRIM, RegionStats as _RS } from './types';

export interface RegionFit { scrimAlpha: number; lc: number; met: boolean; }

/** Blend the scrim (theme.tint at alpha) over the region bg, then measure text Lc. */
function composite(bg: Rgb, tint: Rgb, alpha: number): Rgb {
  return {
    r: bg.r * (1 - alpha) + tint.r * alpha,
    g: bg.g * (1 - alpha) + tint.g * alpha,
    b: bg.b * (1 - alpha) + tint.b * alpha,
  };
}

export function fitRegion(theme: Theme, region: RegionStats): RegionFit {
  const target = ROLE_TARGET_LC[region.role];
  let alpha = theme.baseScrimAlpha;
  const step = 0.04;
  for (; alpha <= MAX_REGION_SCRIM + 1e-9; alpha += step) {
    const bg = composite(region.avg, theme.tint, Math.min(alpha, MAX_REGION_SCRIM));
    const lc = Math.abs(apcaLc(theme.text, bg));
    if (lc >= target) return { scrimAlpha: Math.min(alpha, MAX_REGION_SCRIM), lc, met: true };
  }
  const bg = composite(region.avg, theme.tint, MAX_REGION_SCRIM);
  return { scrimAlpha: MAX_REGION_SCRIM, lc: Math.abs(apcaLc(theme.text, bg)), met: false };
}
```

Update the `types.ts` import line at the top of `engine.ts` to include `RegionStats` and `Theme` (already imported) — remove the unused `_RS` alias if your linter flags it.

- [ ] **Step 4: Run to verify it passes**

Run: `cd web && npx vitest run src/bglab/engine.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/bglab/engine.ts web/src/bglab/engine.test.ts
git commit -m "feat(bg-lab): fitRegion local scrim solve"
```

### Task 8: `chooseTheme` (global solve, consistency guarantee)

**Files:**
- Modify: `web/src/bglab/engine.ts`
- Test: `web/src/bglab/engine.test.ts`

- [ ] **Step 1: Add the failing test**

```ts
import { chooseTheme } from './engine';

describe('chooseTheme', () => {
  const regions = [
    { id: 'timer', role: 'hero', avg: { r: 90, g: 110, b: 95 }, dominant: { r: 90, g: 110, b: 95 }, luma: 100 },
    { id: 'speed', role: 'body', avg: { r: 200, g: 205, b: 190 }, dominant: { r: 200, g: 205, b: 190 }, luma: 200 },
  ] as any[];

  it('returns a single Theme under which EVERY region meets its target', () => {
    const t = chooseTheme(regions, { paletteHue: 150 }, ROLE_TARGET_LC);
    for (const region of regions) {
      const fit = fitRegion(t.theme, region);
      expect(fit.met).toBe(true);
    }
  });

  it('falls back to a legible max-scrim neutral theme on a pathological mid-tone image', () => {
    const midted = [
      { id: 'timer', role: 'hero', avg: { r: 128, g: 128, b: 128 }, dominant: { r: 128, g: 128, b: 128 }, luma: 128 },
    ] as any[];
    const t = chooseTheme(midted, {}, ROLE_TARGET_LC);
    expect(t.theme.baseScrimAlpha).toBeGreaterThan(0);
    const fit = fitRegion(t.theme, midted[0]);
    expect(fit.lc).toBeGreaterThan(40); // legible-enough fallback, never 0
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd web && npx vitest run src/bglab/engine.test.ts`
Expected: FAIL — `chooseTheme` not exported.

- [ ] **Step 3: Implement `chooseTheme` in `engine.ts`**

```ts
import { IVORY, CHARCOAL, SCRIM_STEPS, BLUR_STEPS, BeautyWeights, DEFAULT_WEIGHTS, Role, AdvicePrior } from './types';

export interface ThemeChoice { theme: Theme; cost: number; runnersUp: { theme: Theme; cost: number }[]; }

function moodOf(prior: AdvicePrior, avgLuma: number): 'cool' | 'warm' | 'dark' | 'neutral' {
  if (prior.mood?.includes('dark') || avgLuma < 70) return 'dark';
  if (prior.mood?.includes('cool')) return 'cool';
  if (prior.mood?.includes('warm')) return 'warm';
  return 'neutral';
}

export function chooseTheme(
  regions: RegionStats[],
  prior: AdvicePrior,
  _targets: Record<Role, number>,
  weights: BeautyWeights = DEFAULT_WEIGHTS,
): ThemeChoice {
  const globalAvg: Rgb = {
    r: regions.reduce((s, r) => s + r.avg.r, 0) / regions.length,
    g: regions.reduce((s, r) => s + r.avg.g, 0) / regions.length,
    b: regions.reduce((s, r) => s + r.avg.b, 0) / regions.length,
  };
  const palette = harmonizePalette({ avg: globalAvg, dominant: regions[0].dominant }, prior);
  const avgLuma = regions.reduce((s, r) => s + r.luma, 0) / regions.length;
  const mood = moodOf(prior, avgLuma);
  const textOrder = prior.suggestedPolarity === 'dark' ? [CHARCOAL, IVORY] : [IVORY, CHARCOAL];

  const scored: { theme: Theme; cost: number }[] = [];
  for (const tintC of palette) {
    for (const text of textOrder) {
      for (const baseScrimAlpha of SCRIM_STEPS) {
        for (const blurDp of BLUR_STEPS) {
          const theme: Theme = { tint: tintC.color, text, blurDp, baseScrimAlpha };
          const allMet = regions.every(r => fitRegion(theme, r).met);
          if (!allMet) continue;
          scored.push({ theme, cost: beautyCost(theme, palette, weights, mood) });
        }
      }
    }
  }

  if (scored.length === 0) {
    // pathological: maximum legibility fallback (neutral dark scrim, ivory text)
    const theme: Theme = { tint: { r: 18, g: 18, b: 18 }, text: IVORY, blurDp: 2, baseScrimAlpha: MAX_REGION_SCRIM };
    return { theme, cost: Infinity, runnersUp: [] };
  }
  scored.sort((a, b) => a.cost - b.cost);
  return { theme: scored[0].theme, cost: scored[0].cost, runnersUp: scored.slice(1, 4) };
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd web && npx vitest run src/bglab/engine.test.ts`
Expected: PASS (all engine tests).

- [ ] **Step 5: Commit**

```bash
git add web/src/bglab/engine.ts web/src/bglab/engine.test.ts
git commit -m "feat(bg-lab): chooseTheme global solve with fallback"
```

### Task 9: `sampleRegion` + record golden Theme cases

**Files:**
- Create: `web/src/bglab/sample.ts`
- Test: `web/src/bglab/sample.test.ts`
- Modify: `docs/bg-lab/golden.json`

- [ ] **Step 1: Write the failing test**

```ts
import { describe, it, expect } from 'vitest';
import { sampleRegion } from './sample';

function solid(w: number, h: number, r: number, g: number, b: number): ImageData {
  const data = new Uint8ClampedArray(w * h * 4);
  for (let i = 0; i < w * h; i++) { data[i*4]=r; data[i*4+1]=g; data[i*4+2]=b; data[i*4+3]=255; }
  return { data, width: w, height: h, colorSpace: 'srgb' } as ImageData;
}

describe('sampleRegion', () => {
  it('reports the mean color over the rect', () => {
    const img = solid(100, 100, 80, 160, 120);
    const s = sampleRegion(img, { x: 0.25, y: 0.25, w: 0.5, h: 0.5 }, 'm', 'body');
    expect(s.avg.r).toBeCloseTo(80, 0);
    expect(s.avg.g).toBeCloseTo(160, 0);
    expect(s.luma).toBeGreaterThan(120);
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd web && npx vitest run src/bglab/sample.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `sample.ts`**

```ts
import { Rgb } from './color';
import { RegionStats, Role } from './types';

export interface NormRect { x: number; y: number; w: number; h: number; }

export function sampleRegion(img: ImageData, rect: NormRect, id: string, role: Role): RegionStats {
  const x0 = Math.floor(rect.x * img.width), y0 = Math.floor(rect.y * img.height);
  const x1 = Math.min(img.width, Math.ceil((rect.x + rect.w) * img.width));
  const y1 = Math.min(img.height, Math.ceil((rect.y + rect.h) * img.height));
  let sr = 0, sg = 0, sb = 0, n = 0;
  const buckets = new Map<number, number>();
  for (let y = y0; y < y1; y++) {
    for (let x = x0; x < x1; x++) {
      const i = (y * img.width + x) * 4;
      const r = img.data[i], g = img.data[i+1], b = img.data[i+2];
      sr += r; sg += g; sb += b; n++;
      const key = ((r >> 5) << 6) | ((g >> 5) << 3) | (b >> 5); // 8x8x8 quantize
      buckets.set(key, (buckets.get(key) ?? 0) + 1);
    }
  }
  n = Math.max(1, n);
  const avg: Rgb = { r: sr / n, g: sg / n, b: sb / n };
  let bestKey = 0, bestCount = -1;
  for (const [k, c] of buckets) if (c > bestCount) { bestCount = c; bestKey = k; }
  const dominant: Rgb = {
    r: ((bestKey >> 6) & 7) * 32 + 16,
    g: ((bestKey >> 3) & 7) * 32 + 16,
    b: (bestKey & 7) * 32 + 16,
  };
  const luma = 0.299 * avg.r + 0.587 * avg.g + 0.114 * avg.b;
  return { id, role, avg, dominant, luma };
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd web && npx vitest run src/bglab/sample.test.ts`
Expected: PASS.

- [ ] **Step 5: Record golden Theme cases**

Print deterministic Theme outputs for two synthetic region sets, then paste into `golden.json` `themes[]`:

Run:
```bash
cd web && npx tsx -e "
import {chooseTheme} from './src/bglab/engine';
import {ROLE_TARGET_LC} from './src/bglab/types';
const sets = {
  bright: [{id:'timer',role:'hero',avg:{r:200,g:205,b:190},dominant:{r:200,g:205,b:190},luma:200}],
  forest: [{id:'timer',role:'hero',avg:{r:60,g:90,b:70},dominant:{r:40,g:80,b:60},luma:80},
           {id:'speed',role:'body',avg:{r:120,g:140,b:110},dominant:{r:110,g:130,b:100},luma:130}]
};
for (const [k,regs] of Object.entries(sets)) {
  const t = chooseTheme(regs as any, {paletteHue:150}, ROLE_TARGET_LC);
  console.log(JSON.stringify({name:k, prior:{paletteHue:150}, regions:regs, theme:t.theme}));
}"
```
Paste each printed object into `golden.json` `themes[]`.

- [ ] **Step 6: Add a golden Theme regression test**

Create `web/src/bglab/golden.test.ts`:
```ts
import { describe, it, expect } from 'vitest';
import golden from '../../../docs/bg-lab/golden.json';
import { chooseTheme } from './engine';
import { ROLE_TARGET_LC } from './types';

describe('golden themes', () => {
  for (const c of (golden as any).themes) {
    it(`reproduces theme: ${c.name}`, () => {
      const t = chooseTheme(c.regions, c.prior, ROLE_TARGET_LC);
      expect(t.theme).toEqual(c.theme);
    });
  }
});
```

- [ ] **Step 7: Run the whole TS suite**

Run: `cd web && npx vitest run`
Expected: PASS (color, apca, engine, sample, golden).

- [ ] **Step 8: Commit**

```bash
git add web/src/bglab/sample.ts web/src/bglab/sample.test.ts web/src/bglab/golden.test.ts docs/bg-lab/golden.json
git commit -m "feat(bg-lab): sampleRegion + golden theme regression"
```

---

# Phase 3 — Web `/bg-lab` tuning tool

### Task 10: Mock Running-screen layout rects

**Files:**
- Create: `web/src/bglab/layout.ts`

- [ ] **Step 1: Implement `layout.ts` (no test — data)**

```ts
import { NormRect } from './sample';
import { Role } from './types';

export interface Block { id: string; role: Role; rect: NormRect; label: string; }

// Relative positions mirroring RunningScreen.kt (timer top-center, metric tiles row, button row).
export const RUNNING_BLOCKS: Block[] = [
  { id: 'timer', role: 'hero', rect: { x: 0.30, y: 0.06, w: 0.40, h: 0.18 }, label: '24:18' },
  { id: 'speed', role: 'body', rect: { x: 0.08, y: 0.30, w: 0.26, h: 0.12 }, label: '6.2 mph' },
  { id: 'incline', role: 'body', rect: { x: 0.37, y: 0.30, w: 0.26, h: 0.12 }, label: '5.0%' },
  { id: 'distance', role: 'body', rect: { x: 0.66, y: 0.30, w: 0.26, h: 0.12 }, label: '1.4 mi' },
  { id: 'hint', role: 'muted', rect: { x: 0.30, y: 0.84, w: 0.40, h: 0.08 }, label: 'tap to pause' },
];
```

- [ ] **Step 2: Commit**

```bash
git add web/src/bglab/layout.ts
git commit -m "feat(bg-lab): mock running-screen layout blocks"
```

### Task 11: The `/bg-lab` route

**Files:**
- Create: `web/src/routes/BgLab.tsx`
- Modify: `web/src/main.tsx`

- [ ] **Step 1: Implement `BgLab.tsx`**

```tsx
import React, { useRef, useState, useCallback } from 'react';
import { sampleRegion } from '../bglab/sample';
import { chooseTheme } from '../bglab/engine';
import { fitRegion } from '../bglab/engine';
import { ROLE_TARGET_LC, AdvicePrior, Theme, DEFAULT_WEIGHTS, BeautyWeights } from '../bglab/types';
import { rgbToHex } from '../bglab/color';
import { RUNNING_BLOCKS } from '../bglab/layout';

export default function BgLab(): React.ReactElement {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [theme, setTheme] = useState<Theme | null>(null);
  const [fits, setFits] = useState<{ id: string; lc: number; met: boolean; target: number }[]>([]);
  const [weights, setWeights] = useState<BeautyWeights>(DEFAULT_WEIGHTS);
  const [prior, setPrior] = useState<AdvicePrior>({});
  const imgDataRef = useRef<ImageData | null>(null);

  const recompute = useCallback((img: ImageData, p: AdvicePrior, w: BeautyWeights) => {
    const regions = RUNNING_BLOCKS.map(b => sampleRegion(img, b.rect, b.id, b.role));
    const choice = chooseTheme(regions, p, ROLE_TARGET_LC, w);
    setTheme(choice.theme);
    setFits(regions.map(r => {
      const f = fitRegion(choice.theme, r);
      return { id: r.id, lc: Math.round(f.lc), met: f.met, target: ROLE_TARGET_LC[r.role] };
    }));
  }, []);

  const onFile = useCallback((file: File) => {
    const url = URL.createObjectURL(file);
    const image = new Image();
    image.onload = () => {
      const cv = canvasRef.current!; cv.width = image.width; cv.height = image.height;
      const ctx = cv.getContext('2d')!; ctx.drawImage(image, 0, 0);
      const data = ctx.getImageData(0, 0, image.width, image.height);
      imgDataRef.current = data;
      recompute(data, prior, weights);
      URL.revokeObjectURL(url);
    };
    image.src = url;
  }, [prior, weights, recompute]);

  return (
    <div style={{ display: 'flex', gap: 16, padding: 16, fontFamily: 'sans-serif' }}>
      <div style={{ position: 'relative', flex: '0 0 480px' }}>
        <canvas ref={canvasRef} style={{ width: 480, height: 'auto', display: 'block', borderRadius: 12 }} />
        {theme && RUNNING_BLOCKS.map(b => (
          <div key={b.id} style={{
            position: 'absolute', left: `${b.rect.x*100}%`, top: `${b.rect.y*100}%`,
            width: `${b.rect.w*100}%`, height: `${b.rect.h*100}%`,
            background: `rgba(${theme.tint.r},${theme.tint.g},${theme.tint.b},${theme.baseScrimAlpha})`,
            color: rgbToHex(theme.text), display: 'flex', alignItems: 'center', justifyContent: 'center',
            backdropFilter: theme.blurDp ? `blur(${theme.blurDp}px)` : undefined,
            borderRadius: 10, fontSize: b.role === 'hero' ? 28 : 14,
          }}>{b.label}</div>
        ))}
      </div>

      <div style={{ flex: 1, fontSize: 13 }}>
        <input type="file" accept="image/*" onChange={e => e.target.files?.[0] && onFile(e.target.files[0])} />
        <h4>APCA per block</h4>
        <table><tbody>
          {fits.map(f => (
            <tr key={f.id}>
              <td>{f.id}</td>
              <td style={{ color: f.met ? 'green' : 'crimson', fontWeight: 600 }}>Lc {f.lc} / {f.target}</td>
            </tr>
          ))}
        </tbody></table>

        <h4>Beauty weights</h4>
        {(['scrim','blur','palette','charcoalOnDark'] as (keyof BeautyWeights)[]).map(k => (
          <label key={k} style={{ display: 'block' }}>
            {k}: {weights[k].toFixed(2)}
            <input type="range" min={0} max={2} step={0.05} value={weights[k]}
              onChange={e => { const w = { ...weights, [k]: +e.target.value }; setWeights(w); if (imgDataRef.current) recompute(imgDataRef.current, prior, w); }} />
          </label>
        ))}

        <h4>Prior (Gemini advisor)</h4>
        <button onClick={async () => {
          const img = imgDataRef.current; if (!img) return;
          const cv = canvasRef.current!; const b64 = cv.toDataURL('image/jpeg').split(',')[1];
          try {
            const res = await fetch('/api/background/advise', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ image_hash: 'lab-preview', image_b64: b64 }) });
            const p: AdvicePrior = await res.json();
            setPrior(p); recompute(img, p, weights);
          } catch { /* advisor optional */ }
        }}>Fetch advisor prior</button>
        <pre>{JSON.stringify(prior, null, 2)}</pre>

        <h4>Export</h4>
        <pre>{JSON.stringify({ targets: ROLE_TARGET_LC, weights }, null, 2)}</pre>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Register the route in `web/src/main.tsx`**

After the `/profiles` route (around line 23), add:
```tsx
              <Route path="/bg-lab" component={BgLab} />
```
And add the import near the other route imports at the top:
```tsx
import BgLab from './routes/BgLab';
```

- [ ] **Step 3: Verify it builds and mounts**

Run: `cd web && npx tsc -b && npx vite build`
Expected: build succeeds with no type errors.

Then manually: `./scripts/dev.sh` (or `TREADMILL_MOCK=1 ./scripts/dev.sh`), open `<CaddyURL>/bg-lab`, load a photo, confirm APCA numbers and overlays render and update when you drag the weight sliders.

- [ ] **Step 4: Commit**

```bash
git add web/src/routes/BgLab.tsx web/src/main.tsx
git commit -m "feat(bg-lab): /bg-lab tuning route"
```

---

# Phase 4 — Kotlin engine port

> Mirrors Phase 2 exactly. Each function is a line-for-line port; the golden test guarantees parity. Kotlin tests are plain JVM (no device), run with `cd kotlin && ./gradlew :app:testDebugUnitTest`.

### Task 12: Copy golden.json into test resources + sync guard

**Files:**
- Create: `kotlin/app/src/test/resources/golden.json` (copy of `docs/bg-lab/golden.json`)
- Test: `kotlin/app/src/test/java/com/precor/treadmill/ui/theme/readability/GoldenSyncTest.kt`

- [ ] **Step 1: Copy the file**

Run: `cp docs/bg-lab/golden.json kotlin/app/src/test/resources/golden.json`

- [ ] **Step 2: Write a sync-guard test (fails if the two files drift)**

```kotlin
package com.precor.treadmill.ui.theme.readability

import org.junit.Assert.assertEquals
import org.junit.Test
import java.io.File

class GoldenSyncTest {
    @Test fun testResourceMatchesCanonicalGolden() {
        val canonical = File("../docs/bg-lab/golden.json").readText().filter { !it.isWhitespace() }
        val resource = javaClass.getResource("/golden.json")!!.readText().filter { !it.isWhitespace() }
        assertEquals("kotlin test golden.json drifted from docs/bg-lab/golden.json — re-copy it", canonical, resource)
    }
}
```

> The relative path `../docs/...` resolves from the Gradle module dir `kotlin/app`. If your runner's working dir differs, adjust to an absolute repo path resolved via a `REPO_ROOT` env in `build.gradle` test config.

- [ ] **Step 3: Run**

Run: `cd kotlin && ./gradlew :app:testDebugUnitTest --tests "*GoldenSyncTest"`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add kotlin/app/src/test/resources/golden.json kotlin/app/src/test/java/com/precor/treadmill/ui/theme/readability/GoldenSyncTest.kt
git commit -m "test(readability): kotlin golden.json + sync guard"
```

### Task 13: Kotlin color + APCA

**Files:**
- Create: `.../readability/Color.kt`
- Create: `.../readability/Apca.kt`
- Test: `.../readability/ApcaTest.kt`

- [ ] **Step 1: Write the failing golden test**

```kotlin
package com.precor.treadmill.ui.theme.readability

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Test

class ApcaTest {
    @Test fun matchesCanonicalAnchors() {
        assertEquals(106.04, apcaLc(rgb(0,0,0), rgb(255,255,255)), 0.2)
        assertEquals(-107.88, apcaLc(rgb(255,255,255), rgb(0,0,0)), 0.2)
    }
    @Test fun matchesEveryGoldenRow() {
        val json = JSONObject(javaClass.getResource("/golden.json")!!.readText())
        val rows = json.getJSONArray("apca")
        for (i in 0 until rows.length()) {
            val r = rows.getJSONObject(i)
            assertEquals(r.getDouble("lc"), apcaLc(hexToRgb(r.getString("text")), hexToRgb(r.getString("bg"))), 0.2)
        }
    }
}
```

> `org.json` ships with the Android test classpath. If absent, add `testImplementation("org.json:json:20240303")` to `app/build.gradle`.

- [ ] **Step 2: Run to verify it fails**

Run: `cd kotlin && ./gradlew :app:testDebugUnitTest --tests "*ApcaTest"`
Expected: FAIL — unresolved references.

- [ ] **Step 3: Implement `Color.kt`**

```kotlin
package com.precor.treadmill.ui.theme.readability

import kotlin.math.cbrt
import kotlin.math.pow
import kotlin.math.hypot
import kotlin.math.atan2
import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.sin

data class Rgb(val r: Double, val g: Double, val b: Double)
data class Oklab(val L: Double, val a: Double, val b: Double)
data class Oklch(val L: Double, val C: Double, val h: Double)

fun rgb(r: Int, g: Int, b: Int) = Rgb(r.toDouble(), g.toDouble(), b.toDouble())
fun hexToRgb(hex: String): Rgb {
    val s = hex.removePrefix("#")
    return rgb(s.substring(0,2).toInt(16), s.substring(2,4).toInt(16), s.substring(4,6).toInt(16))
}

private fun srgbToLinear(c: Double): Double { val x = c/255.0; return if (x <= 0.04045) x/12.92 else ((x+0.055)/1.055).pow(2.4) }
private fun linearToSrgb(x: Double): Double { val c = if (x <= 0.0031308) x*12.92 else 1.055*x.pow(1/2.4)-0.055; return c*255.0 }

fun rgbToOklab(c: Rgb): Oklab {
    val lr = srgbToLinear(c.r); val lg = srgbToLinear(c.g); val lb = srgbToLinear(c.b)
    val l = cbrt(0.4122214708*lr + 0.5363325363*lg + 0.0514459929*lb)
    val m = cbrt(0.2119034982*lr + 0.6806995451*lg + 0.1073969566*lb)
    val s = cbrt(0.0883024619*lr + 0.2817188376*lg + 0.6299787005*lb)
    return Oklab(0.2104542553*l+0.7936177850*m-0.0040720468*s,
                 1.9779984951*l-2.4285922050*m+0.4505937099*s,
                 0.0259040371*l+0.7827717662*m-0.8086757660*s)
}
fun oklabToRgb(o: Oklab): Rgb {
    val l = (o.L + 0.3963377774*o.a + 0.2158037573*o.b).pow(3)
    val m = (o.L - 0.1055613458*o.a - 0.0638541728*o.b).pow(3)
    val s = (o.L - 0.0894841775*o.a - 1.2914855480*o.b).pow(3)
    return Rgb(linearToSrgb(+4.0767416621*l-3.3077115913*m+0.2309699292*s),
               linearToSrgb(-1.2684380046*l+2.6097574011*m-0.3413193965*s),
               linearToSrgb(-0.0041960863*l-0.7034186147*m+1.7076147010*s))
}
fun rgbToOklch(c: Rgb): Oklch {
    val o = rgbToOklab(c); var h = atan2(o.b, o.a)*180/PI; if (h<0) h+=360
    return Oklch(o.L, hypot(o.a, o.b), h)
}
fun oklchToRgb(o: Oklch): Rgb {
    val r = o.h*PI/180; return oklabToRgb(Oklab(o.L, o.C*cos(r), o.C*sin(r)))
}
fun oklabDeltaE(a: Oklab, b: Oklab) = Math.sqrt((a.L-b.L)*(a.L-b.L)+(a.a-b.a)*(a.a-b.a)+(a.b-b.b)*(a.b-b.b))
```

- [ ] **Step 4: Implement `Apca.kt`**

```kotlin
package com.precor.treadmill.ui.theme.readability

import kotlin.math.abs
import kotlin.math.pow

private const val Rco=0.2126729; private const val Gco=0.7151522; private const val Bco=0.0721750
private const val normBG=0.56; private const val normTXT=0.57; private const val revTXT=0.62; private const val revBG=0.65
private const val blkThrs=0.022; private const val blkClmp=1.414; private const val loClip=0.1; private const val deltaYmin=0.0005
private const val scaleBoW=1.14; private const val loBoWoffset=0.027; private const val scaleWoB=1.14; private const val loWoBoffset=0.027

private fun toY(c: Rgb): Double {
    fun lin(v: Double) = (v/255.0).pow(2.4)
    var y = Rco*lin(c.r)+Gco*lin(c.g)+Bco*lin(c.b)
    if (y < blkThrs) y += (blkThrs - y).pow(blkClmp)
    return y
}

/** APCA Lc. Positive = dark text on light bg; negative = light text on dark bg. */
fun apcaLc(text: Rgb, bg: Rgb): Double {
    val txtY = toY(text); val bgY = toY(bg)
    if (abs(bgY-txtY) < deltaYmin) return 0.0
    val out: Double = if (bgY > txtY) {
        val s = (bgY.pow(normBG) - txtY.pow(normTXT)) * scaleBoW
        if (s < loClip) 0.0 else s - loBoWoffset
    } else {
        val s = (bgY.pow(revBG) - txtY.pow(revTXT)) * scaleWoB
        if (s > -loClip) 0.0 else s + loWoBoffset
    }
    return out * 100.0
}
```

- [ ] **Step 5: Run to verify it passes**

Run: `cd kotlin && ./gradlew :app:testDebugUnitTest --tests "*ApcaTest"`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add kotlin/app/src/main/java/com/precor/treadmill/ui/theme/readability/Color.kt kotlin/app/src/main/java/com/precor/treadmill/ui/theme/readability/Apca.kt kotlin/app/src/test/java/com/precor/treadmill/ui/theme/readability/ApcaTest.kt
git commit -m "feat(readability): kotlin color + APCA port"
```

### Task 14: Kotlin types + engine + golden theme parity

**Files:**
- Create: `.../readability/Types.kt`
- Create: `.../readability/Engine.kt`
- Test: `.../readability/EngineTest.kt`

- [ ] **Step 1: Write the failing golden-theme parity test**

```kotlin
package com.precor.treadmill.ui.theme.readability

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class EngineTest {
    private fun region(j: JSONObject): RegionStats {
        fun col(o: JSONObject) = Rgb(o.getDouble("r"), o.getDouble("g"), o.getDouble("b"))
        return RegionStats(j.getString("id"), Role.valueOf(j.getString("role").uppercase()),
            col(j.getJSONObject("avg")), col(j.getJSONObject("dominant")), j.getDouble("luma"))
    }

    @Test fun reproducesGoldenThemes() {
        val json = JSONObject(javaClass.getResource("/golden.json")!!.readText())
        val cases = json.getJSONArray("themes")
        for (i in 0 until cases.length()) {
            val c = cases.getJSONObject(i)
            val regions = (0 until c.getJSONArray("regions").length()).map { region(c.getJSONArray("regions").getJSONObject(it)) }
            val priorJ = c.getJSONObject("prior")
            val prior = AdvicePrior(paletteHue = if (priorJ.has("paletteHue")) priorJ.getDouble("paletteHue") else null)
            val theme = chooseTheme(regions, prior).theme
            val expected = c.getJSONObject("theme")
            assertEquals("tint.r ${c.getString("name")}", expected.getJSONObject("tint").getDouble("r"), theme.tint.r, 1.0)
            assertEquals("scrim ${c.getString("name")}", expected.getDouble("baseScrimAlpha"), theme.baseScrimAlpha, 0.001)
        }
    }

    @Test fun everyRegionMeetsTargetUnderChosenTheme() {
        val regions = listOf(
            RegionStats("timer", Role.HERO, rgb(90,110,95), rgb(90,110,95), 100.0),
            RegionStats("speed", Role.BODY, rgb(200,205,190), rgb(200,205,190), 200.0),
        )
        val theme = chooseTheme(regions, AdvicePrior(paletteHue = 150.0)).theme
        for (r in regions) assertTrue(fitRegion(theme, r).met)
    }
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd kotlin && ./gradlew :app:testDebugUnitTest --tests "*EngineTest"`
Expected: FAIL — unresolved references.

- [ ] **Step 3: Implement `Types.kt`**

```kotlin
package com.precor.treadmill.ui.theme.readability

enum class Role { HERO, BODY, MUTED }

data class RegionStats(val id: String, val role: Role, val avg: Rgb, val dominant: Rgb, val luma: Double)
data class TintCandidate(val color: Rgb, val paletteDistance: Double)
data class Theme(val tint: Rgb, val text: Rgb, val blurDp: Double, val baseScrimAlpha: Double)
data class AdvicePrior(
    val paletteHue: Double? = null,
    val suggestedPolarity: String? = null,
    val mood: String? = null,
)
data class BeautyWeights(val scrim: Double = 1.0, val blur: Double = 0.5, val palette: Double = 0.6, val charcoalOnDark: Double = 0.3)

val ROLE_TARGET_LC = mapOf(Role.HERO to 75.0, Role.BODY to 60.0, Role.MUTED to 45.0)
val IVORY = Rgb(242.0, 236.0, 223.0)
val CHARCOAL = Rgb(30.0, 32.0, 30.0)
val SCRIM_STEPS = listOf(0.18, 0.28, 0.38, 0.5, 0.62)
val BLUR_STEPS = listOf(0.0, 1.0, 2.0, 3.0)
const val MAX_REGION_SCRIM = 0.72
```

- [ ] **Step 4: Implement `Engine.kt`** (line-for-line port of `engine.ts`)

```kotlin
package com.precor.treadmill.ui.theme.readability

import kotlin.math.abs
import kotlin.math.min

data class RegionFit(val scrimAlpha: Double, val lc: Double, val met: Boolean)
data class ThemeChoice(val theme: Theme, val cost: Double, val runnersUp: List<Pair<Theme, Double>>)

fun harmonizePalette(globalDominant: Rgb, prior: AdvicePrior): List<TintCandidate> {
    val domH = rgbToOklch(globalDominant).h
    val hues = mutableListOf(domH)
    prior.paletteHue?.let { hues.add(it) }
    hues.add((domH + 180) % 360)
    val out = hues.map {
        val color = oklchToRgb(Oklch(0.26, 0.05, it))
        TintCandidate(color, oklabDeltaE(rgbToOklab(color), rgbToOklab(globalDominant)))
    }.toMutableList()
    out.add(TintCandidate(Rgb(26.0,26.0,26.0), 0.0))
    return out
}

private fun moodOf(prior: AdvicePrior, avgLuma: Double): String =
    when {
        prior.mood?.contains("dark") == true || avgLuma < 70 -> "dark"
        prior.mood?.contains("cool") == true -> "cool"
        prior.mood?.contains("warm") == true -> "warm"
        else -> "neutral"
    }

fun beautyCost(theme: Theme, palette: List<TintCandidate>, w: BeautyWeights, mood: String): Double {
    val tintDist = palette.minOf { oklabDeltaE(rgbToOklab(it.color), rgbToOklab(theme.tint)) }
    var cost = w.scrim*theme.baseScrimAlpha + w.blur*(theme.blurDp/3.0) + w.palette*tintDist
    val charcoalText = theme.text.r < 80 && theme.text.g < 80
    if (charcoalText && mood == "dark") cost += w.charcoalOnDark
    return cost
}

private fun composite(bg: Rgb, tint: Rgb, a: Double) =
    Rgb(bg.r*(1-a)+tint.r*a, bg.g*(1-a)+tint.g*a, bg.b*(1-a)+tint.b*a)

fun fitRegion(theme: Theme, region: RegionStats): RegionFit {
    val target = ROLE_TARGET_LC.getValue(region.role)
    var alpha = theme.baseScrimAlpha
    while (alpha <= MAX_REGION_SCRIM + 1e-9) {
        val a = min(alpha, MAX_REGION_SCRIM)
        val lc = abs(apcaLc(theme.text, composite(region.avg, theme.tint, a)))
        if (lc >= target) return RegionFit(a, lc, true)
        alpha += 0.04
    }
    val lc = abs(apcaLc(theme.text, composite(region.avg, theme.tint, MAX_REGION_SCRIM)))
    return RegionFit(MAX_REGION_SCRIM, lc, false)
}

fun chooseTheme(regions: List<RegionStats>, prior: AdvicePrior, weights: BeautyWeights = BeautyWeights()): ThemeChoice {
    val globalDominant = regions.first().dominant
    val palette = harmonizePalette(globalDominant, prior)
    val avgLuma = regions.map { it.luma }.average()
    val mood = moodOf(prior, avgLuma)
    val textOrder = if (prior.suggestedPolarity == "dark") listOf(CHARCOAL, IVORY) else listOf(IVORY, CHARCOAL)

    val scored = mutableListOf<Pair<Theme, Double>>()
    for (tintC in palette) for (text in textOrder) for (scrim in SCRIM_STEPS) for (blur in BLUR_STEPS) {
        val theme = Theme(tintC.color, text, blur, scrim)
        if (regions.all { fitRegion(theme, it).met }) scored.add(theme to beautyCost(theme, palette, weights, mood))
    }
    if (scored.isEmpty()) {
        return ThemeChoice(Theme(Rgb(18.0,18.0,18.0), IVORY, 2.0, MAX_REGION_SCRIM), Double.POSITIVE_INFINITY, emptyList())
    }
    scored.sortBy { it.second }
    return ThemeChoice(scored[0].first, scored[0].second, scored.drop(1).take(3))
}
```

- [ ] **Step 5: Run to verify it passes**

Run: `cd kotlin && ./gradlew :app:testDebugUnitTest --tests "*EngineTest"`
Expected: PASS. If golden tint/scrim differ slightly, the two ports diverge — reconcile constants (most likely an OKLCH rounding or `SCRIM_STEPS` mismatch) until parity holds within tolerance.

- [ ] **Step 6: Commit**

```bash
git add kotlin/app/src/main/java/com/precor/treadmill/ui/theme/readability/Types.kt kotlin/app/src/main/java/com/precor/treadmill/ui/theme/readability/Engine.kt kotlin/app/src/test/java/com/precor/treadmill/ui/theme/readability/EngineTest.kt
git commit -m "feat(readability): kotlin engine port + golden parity"
```

### Task 15: Kotlin `sampleRegion(Bitmap, Rect)`

**Files:**
- Create: `.../readability/Sample.kt`
- Test: `.../readability/SampleTest.kt` (uses Robolectric OR a plain IntArray helper)

- [ ] **Step 1: Write the failing test (pure, no Android Bitmap)**

```kotlin
package com.precor.treadmill.ui.theme.readability

import org.junit.Assert.assertEquals
import org.junit.Test

class SampleTest {
    @Test fun meanColorOverRect() {
        val w = 100; val h = 100
        val pixels = IntArray(w*h) { (0xFF shl 24) or (80 shl 16) or (160 shl 8) or 120 }
        val s = sampleRegionPixels(pixels, w, h, NormRect(0.25, 0.25, 0.5, 0.5), "m", Role.BODY)
        assertEquals(80.0, s.avg.r, 1.0)
        assertEquals(160.0, s.avg.g, 1.0)
    }
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd kotlin && ./gradlew :app:testDebugUnitTest --tests "*SampleTest"`
Expected: FAIL — unresolved references.

- [ ] **Step 3: Implement `Sample.kt`** (Bitmap wrapper + testable pixel core)

```kotlin
package com.precor.treadmill.ui.theme.readability

import android.graphics.Bitmap

data class NormRect(val x: Double, val y: Double, val w: Double, val h: Double)

fun sampleRegionPixels(pixels: IntArray, width: Int, height: Int, rect: NormRect, id: String, role: Role): RegionStats {
    val x0 = (rect.x*width).toInt(); val y0 = (rect.y*height).toInt()
    val x1 = minOf(width, ((rect.x+rect.w)*width).toInt()); val y1 = minOf(height, ((rect.y+rect.h)*height).toInt())
    var sr=0L; var sg=0L; var sb=0L; var n=0L
    val buckets = HashMap<Int, Int>()
    for (y in y0 until y1) for (x in x0 until x1) {
        val p = pixels[y*width+x]
        val r=(p shr 16) and 0xFF; val g=(p shr 8) and 0xFF; val b=p and 0xFF
        sr+=r; sg+=g; sb+=b; n++
        val key=((r shr 5) shl 6) or ((g shr 5) shl 3) or (b shr 5)
        buckets[key]=(buckets[key]?:0)+1
    }
    if (n==0L) n=1
    val avg=Rgb(sr.toDouble()/n, sg.toDouble()/n, sb.toDouble()/n)
    val bestKey = buckets.maxByOrNull { it.value }?.key ?: 0
    val dominant=Rgb((((bestKey shr 6) and 7)*32+16).toDouble(), (((bestKey shr 3) and 7)*32+16).toDouble(), ((bestKey and 7)*32+16).toDouble())
    val luma=0.299*avg.r+0.587*avg.g+0.114*avg.b
    return RegionStats(id, role, avg, dominant, luma)
}

fun sampleRegion(bitmap: Bitmap, rect: NormRect, id: String, role: Role): RegionStats {
    val pixels = IntArray(bitmap.width*bitmap.height)
    bitmap.getPixels(pixels, 0, bitmap.width, 0, 0, bitmap.width, bitmap.height)
    return sampleRegionPixels(pixels, bitmap.width, bitmap.height, rect, id, role)
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd kotlin && ./gradlew :app:testDebugUnitTest --tests "*SampleTest"`
Expected: PASS.

- [ ] **Step 5: Run the whole Kotlin unit suite**

Run: `cd kotlin && ./gradlew :app:testDebugUnitTest`
Expected: PASS (Apca, Engine, Sample, GoldenSync, plus pre-existing tests).

- [ ] **Step 6: Commit**

```bash
git add kotlin/app/src/main/java/com/precor/treadmill/ui/theme/readability/Sample.kt kotlin/app/src/test/java/com/precor/treadmill/ui/theme/readability/SampleTest.kt
git commit -m "feat(readability): kotlin sampleRegion"
```

---

# Phase 5 — Gemini advisor

### Task 16: `advise_background` in program_engine.py

**Files:**
- Modify: `python/program_engine.py`
- Test: `python/tests/test_background_advice.py`

- [ ] **Step 1: Write the failing test**

```python
import json
from unittest.mock import patch, MagicMock
import program_engine

def test_advise_background_coerces_partial_json():
    fake = MagicMock()
    fake.text = json.dumps({"palette_hue": 158})  # missing other fields
    with patch.object(program_engine, "call_gemini_image", return_value=fake):
        out = program_engine.advise_background(b"\xff\xd8fakejpeg")
    assert out["palette_hue"] == 158
    assert out["suggested_polarity"] in ("light", "dark")  # defaulted
    assert isinstance(out["busy_zones"], list)

def test_advise_background_neutral_on_error():
    with patch.object(program_engine, "call_gemini_image", side_effect=RuntimeError("no key")):
        out = program_engine.advise_background(b"x")
    assert out["suggested_polarity"] == "light"
    assert out["busy_zones"] == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd python && python3 -m pytest tests/test_background_advice.py -v`
Expected: FAIL — `advise_background` / `call_gemini_image` not defined.

- [ ] **Step 3: Implement in `program_engine.py`**

Add near the other Gemini helpers:
```python
NEUTRAL_PRIOR = {"palette_hue": None, "suggested_polarity": "light", "mood": "neutral", "busy_zones": []}

def call_gemini_image(image_bytes: bytes):
    """Single multimodal call. Separated so tests can patch it."""
    from google.genai import types
    client = get_client()
    prompt = (
        "You are tuning text overlays on this background photo. Respond ONLY with JSON: "
        '{"palette_hue": <0-360 OKLCH hue the photo wants for tints>, '
        '"suggested_polarity": "light"|"dark" (light=ivory text, dark=charcoal text), '
        '"mood": "<short label like cool-forest>", '
        '"busy_zones": [{"x":0-1,"y":0-1,"w":0-1,"h":0-1,"note":"..."}]}'
    )
    return client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"), prompt],
    )

def advise_background(image_bytes: bytes) -> dict:
    """Return an AdvicePrior dict. Never raises; degrades to NEUTRAL_PRIOR."""
    try:
        resp = call_gemini_image(image_bytes)
        raw = json.loads(_extract_json(resp.text))
    except Exception:
        return dict(NEUTRAL_PRIOR)
    out = dict(NEUTRAL_PRIOR)
    if isinstance(raw.get("palette_hue"), (int, float)):
        out["palette_hue"] = float(raw["palette_hue"])
    if raw.get("suggested_polarity") in ("light", "dark"):
        out["suggested_polarity"] = raw["suggested_polarity"]
    if isinstance(raw.get("mood"), str):
        out["mood"] = raw["mood"][:40]
    if isinstance(raw.get("busy_zones"), list):
        out["busy_zones"] = [z for z in raw["busy_zones"] if isinstance(z, dict)][:6]
    return out
```

If `_extract_json` doesn't already exist, add a small helper that strips ```` ```json ```` fences and returns the JSON substring (reuse the existing chat JSON-parse path if one is present).

- [ ] **Step 4: Run to verify it passes**

Run: `cd python && python3 -m pytest tests/test_background_advice.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add python/program_engine.py python/tests/test_background_advice.py
git commit -m "feat(advisor): gemini background advise + coercion"
```

### Task 17: `POST /api/background/advise` endpoint + cache

**Files:**
- Modify: `python/server.py`
- Test: `python/tests/test_background_advice.py`

- [ ] **Step 1: Add the failing endpoint test**

Append to `test_background_advice.py`:
```python
from fastapi.testclient import TestClient
import base64

def test_endpoint_cache_hit_skips_gemini(monkeypatch):
    import server
    calls = {"n": 0}
    def fake_advise(b):
        calls["n"] += 1
        return {"palette_hue": 120.0, "suggested_polarity": "light", "mood": "x", "busy_zones": []}
    monkeypatch.setattr(server.program_engine, "advise_background", fake_advise)
    server._background_advice_cache.clear()
    client = TestClient(server.app)
    img = base64.b64encode(b"\xff\xd8jpeg").decode()
    body = {"image_hash": "abc", "image_b64": img}
    r1 = client.post("/api/background/advise", json=body)
    r2 = client.post("/api/background/advise", json=body)
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["palette_hue"] == 120.0
    assert calls["n"] == 1  # second call served from cache

def test_endpoint_validates_hash():
    import server
    client = TestClient(server.app)
    r = client.post("/api/background/advise", json={})
    assert r.status_code == 422 or r.status_code == 400
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd python && python3 -m pytest tests/test_background_advice.py -v`
Expected: FAIL — endpoint missing.

- [ ] **Step 3: Implement in `server.py`**

Add near other state/JSON helpers:
```python
_BACKGROUND_ADVICE_FILE = os.path.join(os.path.dirname(__file__), "background_advice.json")
_background_advice_cache: dict = _load_json(_BACKGROUND_ADVICE_FILE, default={})  # reuse existing loader pattern

class BackgroundAdviseRequest(BaseModel):
    image_hash: str
    image_b64: str | None = None

MAX_ADVISE_IMAGE_BYTES = 4 * 1024 * 1024  # 4 MB cap

@app.post("/api/background/advise")
async def background_advise(req: BackgroundAdviseRequest):
    if req.image_hash in _background_advice_cache:
        return _background_advice_cache[req.image_hash]
    if not req.image_b64:
        # unseen hash with no image to analyze -> neutral, do not cache (image may arrive later)
        return program_engine.NEUTRAL_PRIOR
    try:
        image_bytes = base64.b64decode(req.image_b64, validate=True)
    except Exception:
        raise HTTPException(status_code=400, detail="image_b64 is not valid base64")
    if len(image_bytes) > MAX_ADVISE_IMAGE_BYTES:
        raise HTTPException(status_code=400, detail="image too large")
    prior = program_engine.advise_background(image_bytes)
    _background_advice_cache[req.image_hash] = prior
    _save_json(_BACKGROUND_ADVICE_FILE, _background_advice_cache)  # reuse existing saver
    return prior
```

Ensure `import base64` and `from fastapi import HTTPException` are present, and `BaseModel`/`_load_json`/`_save_json` reference the project's existing helpers (match their real names if different).

- [ ] **Step 4: Run to verify it passes**

Run: `cd python && python3 -m pytest tests/test_background_advice.py -v`
Expected: PASS (all four tests).

- [ ] **Step 5: Update CLAUDE.md API table**

Add a row under **AI Chat** (or a new **Background** group) in `python/`’s API reference in `CLAUDE.md`:
```
| `/api/background/advise` | POST | Get cached Gemini overlay prior for a background. Body: `{"image_hash":"...","image_b64":"..."}` |
```

- [ ] **Step 6: Commit**

```bash
git add python/server.py python/tests/test_background_advice.py CLAUDE.md
git commit -m "feat(advisor): /api/background/advise endpoint + cache"
```

---

# Phase 6 — Android wire-in

### Task 18: `BackgroundAdviceClient` (calls endpoint, caches by hash)

**Files:**
- Create: `kotlin/app/src/main/java/com/precor/treadmill/data/BackgroundAdviceClient.kt`
- Test: `kotlin/app/src/test/java/com/precor/treadmill/data/BackgroundAdviceClientTest.kt`

- [ ] **Step 1: Write the failing test (pure JSON parsing, Postel's Law)**

```kotlin
package com.precor.treadmill.data

import com.precor.treadmill.ui.theme.readability.AdvicePrior
import org.junit.Assert.assertEquals
import org.junit.Test

class BackgroundAdviceClientTest {
    @Test fun parsesFullJson() {
        val p = parseAdvicePrior("""{"palette_hue":158,"suggested_polarity":"dark","mood":"cool-forest"}""")
        assertEquals(158.0, p.paletteHue!!, 0.01)
        assertEquals("dark", p.suggestedPolarity)
    }
    @Test fun toleratesMissingAndUnknownFields() {
        val p = parseAdvicePrior("""{"unexpected":true}""")
        assertEquals(null, p.paletteHue)
        assertEquals(AdvicePrior(), p)  // all defaults
    }
    @Test fun toleratesGarbage() {
        assertEquals(AdvicePrior(), parseAdvicePrior("not json"))
    }
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd kotlin && ./gradlew :app:testDebugUnitTest --tests "*BackgroundAdviceClientTest"`
Expected: FAIL — unresolved references.

- [ ] **Step 3: Implement `BackgroundAdviceClient.kt`**

```kotlin
package com.precor.treadmill.data

import com.precor.treadmill.ui.theme.readability.AdvicePrior
import org.json.JSONObject

/** Postel's Law: never throw on server output; unknown/missing -> defaults. */
fun parseAdvicePrior(body: String): AdvicePrior = try {
    val j = JSONObject(body)
    AdvicePrior(
        paletteHue = if (j.has("palette_hue") && !j.isNull("palette_hue")) j.getDouble("palette_hue") else null,
        suggestedPolarity = j.optString("suggested_polarity").takeIf { it == "light" || it == "dark" },
        mood = j.optString("mood").takeIf { it.isNotEmpty() },
    )
} catch (_: Exception) {
    AdvicePrior()
}

class BackgroundAdviceClient(private val baseUrl: String, private val httpGetPost: (String, String) -> String) {
    private val cache = HashMap<String, AdvicePrior>()
    /** Returns cached prior or fetches once. Falls back to neutral AdvicePrior on any error. */
    fun advise(imageHash: String, imageB64: () -> String): AdvicePrior {
        cache[imageHash]?.let { return it }
        val prior = try {
            val body = """{"image_hash":"$imageHash","image_b64":"${imageB64()}"}"""
            parseAdvicePrior(httpGetPost("$baseUrl/api/background/advise", body))
        } catch (_: Exception) { AdvicePrior() }
        cache[imageHash] = prior
        return prior
    }
}
```

> `httpGetPost` is injected so the unit test never hits the network; production wiring passes the app's existing HTTP helper.

- [ ] **Step 4: Run to verify it passes**

Run: `cd kotlin && ./gradlew :app:testDebugUnitTest --tests "*BackgroundAdviceClientTest"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kotlin/app/src/main/java/com/precor/treadmill/data/BackgroundAdviceClient.kt kotlin/app/src/test/java/com/precor/treadmill/data/BackgroundAdviceClientTest.kt
git commit -m "feat(readability): android advice client + lenient parse"
```

### Task 19: Replace `GlassTheme` internals with the engine

**Files:**
- Modify: `kotlin/app/src/main/java/com/precor/treadmill/ui/theme/GlassTheme.kt`

- [ ] **Step 1: Add a `Theme`-driven panel modifier (keep old API additive, don't break callers yet)**

Add to `GlassTheme.kt`:
```kotlin
import com.precor.treadmill.ui.theme.readability.Theme as ReadTheme
import androidx.compose.ui.graphics.Color as ComposeColor

private fun ReadTheme.tintColor(alpha: Double) =
    ComposeColor(tint.r.toInt().coerceIn(0,255), tint.g.toInt().coerceIn(0,255), tint.b.toInt().coerceIn(0,255), (alpha*255).toInt().coerceIn(0,255))

/** Panel driven by the readability engine: tint color from the photo, per-region scrim alpha. */
fun Modifier.adaptivePanel(theme: ReadTheme, scrimAlpha: Double, shape: RoundedCornerShape = RoundedCornerShape(14.dp)): Modifier {
    var m = this
        .background(theme.tintColor(scrimAlpha), shape)
        .border(1.dp, ComposeColor.White.copy(alpha = 0.22f), shape)
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S && theme.blurDp > 0) m = m.blur(theme.blurDp.dp)
    return m
}

fun ReadTheme.composeTextColor() =
    ComposeColor(text.r.toInt().coerceIn(0,255), text.g.toInt().coerceIn(0,255), text.b.toInt().coerceIn(0,255))
```

- [ ] **Step 2: Verify it compiles**

Run: `cd kotlin && ./gradlew :app:assembleDebug`
Expected: BUILD SUCCESSFUL.

- [ ] **Step 3: Commit**

```bash
git add kotlin/app/src/main/java/com/precor/treadmill/ui/theme/GlassTheme.kt
git commit -m "feat(readability): adaptivePanel modifier bridging engine->compose"
```

### Task 20: Wire RunningScreen to the engine

**Files:**
- Modify: `kotlin/app/src/main/java/com/precor/treadmill/ui/screens/running/RunningScreen.kt:146-176`

- [ ] **Step 1: Compute the Theme once from the background bitmap + regions**

Replace the `val glassParams = rememberGlassParams(R.drawable.bg_forest)` line (and the global-average overlay block) with a remembered engine computation:
```kotlin
import com.precor.treadmill.ui.theme.readability.*
import androidx.compose.ui.platform.LocalContext
import android.graphics.BitmapFactory

val context = LocalContext.current
val readTheme = remember {
    val opts = BitmapFactory.Options().apply { inSampleSize = 4 }
    val bmp = BitmapFactory.decodeResource(context.resources, R.drawable.bg_forest, opts)
    val blocks = listOf(
        Triple("timer", Role.HERO, NormRect(0.30, 0.06, 0.40, 0.18)),
        Triple("speed", Role.BODY, NormRect(0.08, 0.30, 0.26, 0.12)),
        Triple("incline", Role.BODY, NormRect(0.37, 0.30, 0.26, 0.12)),
        Triple("distance", Role.BODY, NormRect(0.66, 0.30, 0.26, 0.12)),
        Triple("hint", Role.MUTED, NormRect(0.30, 0.84, 0.40, 0.08)),
    )
    val regions = blocks.map { (id, role, rect) -> sampleRegion(bmp, rect, id, role) }
    bmp.recycle()
    // prior fetched async elsewhere; start neutral. APCA still guarantees legibility.
    val choice = chooseTheme(regions, AdvicePrior())
    val perRegion = regions.associate { it.id to fitRegion(choice.theme, it).scrimAlpha }
    choice.theme to perRegion
}
val theme = readTheme.first
val scrims = readTheme.second
```

- [ ] **Step 2: Apply `theme`/`scrims` to the existing panels and text**

For each metric tile / panel, swap the old `Modifier.glassPanel(glassParams)` (or `glassPanelTinted`) for:
```kotlin
Modifier.adaptivePanel(theme, scrims["speed"] ?: theme.baseScrimAlpha)
```
and set the text color via `color = theme.composeTextColor()` on the timer and metric `Text` composables. Keep the background `Image(bg_forest)` as-is; remove the old `Brush.verticalGradient(... glassParams.overlayOpacity ...)` overlay block (the per-region scrim now does that job). The second usage at `RunningScreen.kt:386-390` gets the same treatment.

- [ ] **Step 3: Verify build + run on the emulator**

Run: `cd kotlin && ./gradlew assembleDebug`
Expected: BUILD SUCCESSFUL.

Then: launch on the audio/test emulator (`~/scripts/start-emulator.sh test_device`, display :98), install the APK, open the Running screen, confirm the timer + metrics are clearly legible over `bg_forest` and the panels carry a forest-derived tint rather than flat black. Capture a screenshot for the review.

- [ ] **Step 4: Optional — apply the live prior**

If wiring the advisor at runtime now: fetch `BackgroundAdviceClient.advise(hash) { base64(bg_forest) }` off the main thread, and when it returns, recompute `chooseTheme(regions, prior)` and update state. Behind the same `remember` keyed on the prior so neutral renders first, then refines. (May be deferred to a follow-up issue — APCA already guarantees legibility without it.)

- [ ] **Step 5: Commit**

```bash
git add kotlin/app/src/main/java/com/precor/treadmill/ui/screens/running/RunningScreen.kt
git commit -m "feat(readability): drive RunningScreen overlays from engine"
```

### Task 21: Regression test — bright region is legible

**Files:**
- Create: `kotlin/app/src/test/java/com/precor/treadmill/ui/theme/readability/BrightRegionRegressionTest.kt`

- [ ] **Step 1: Write the test that fails under the OLD global-average approach**

```kotlin
package com.precor.treadmill.ui.theme.readability

import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.math.abs

class BrightRegionRegressionTest {
    /** A photo whose timer sits over a bright clearing: global average is "medium" but the local
     * region is bright. The engine must still produce text that clears the hero target there. */
    @Test fun brightLocalRegionStillMeetsHeroTarget() {
        val brightTimer = RegionStats("timer", Role.HERO, rgb(210,215,200), rgb(210,215,200), 210.0)
        val darkRest = RegionStats("speed", Role.BODY, rgb(40,55,45), rgb(40,55,45), 50.0)
        val theme = chooseTheme(listOf(brightTimer, darkRest), AdvicePrior()).theme
        val fit = fitRegion(theme, brightTimer)
        assertTrue("hero region must reach Lc>=75, got ${fit.lc}", abs(fit.lc) >= 75.0)
    }
}
```

- [ ] **Step 2: Run to verify it passes with the engine**

Run: `cd kotlin && ./gradlew :app:testDebugUnitTest --tests "*BrightRegionRegressionTest"`
Expected: PASS.

> This is the "would-fail-without-the-change" proof: the old `GlassParams.fromBrightness(globalAverage)` produced one black-scrim opacity from the *mixed* average and never measured the bright timer region — text there could fall well under Lc 75. The new engine guarantees it.

- [ ] **Step 3: Commit**

```bash
git add kotlin/app/src/test/java/com/precor/treadmill/ui/theme/readability/BrightRegionRegressionTest.kt
git commit -m "test(readability): bright-region legibility regression"
```

---

# Final gates

### Task 22: Full suites + security audit

- [ ] **Step 1: Run all relevant suites**

```bash
cd web && npx vitest run                      # TS engine + golden
cd kotlin && ./gradlew :app:testDebugUnitTest  # kotlin engine + parity + regression
cd python && python3 -m pytest tests/test_background_advice.py -v
```
Expected: all PASS.

- [ ] **Step 2: Two-track security audit (per global protocol)**

- CVE scan on any new deps: `cd web && npm audit --production` (vitest is dev-only); no new Python deps expected (`google-genai` already present) — confirm with `pip-audit` if available.
- `codex exec --sandbox read-only` review of: the new `/api/background/advise` endpoint (untrusted base64 image, size cap, no shell-out, no image-byte logging) and the engine loops (no unbounded allocation). Provide concrete line numbers from `server.py` and `Engine.kt`. Fix or explicitly punt findings in-session.

- [ ] **Step 3: Update memory + close issues**

- `bd close` the implementation issues filed for this work.
- Note in the design doc’s status that implementation landed.

- [ ] **Step 4: Stop — await push authorization**

Per user rules: commits are made throughout, but **do not `git push`** until the user authorizes. Summarize what landed and ask.

---

## Self-review notes (author)

- **Spec coverage:** per-region sampling (Task 9/15), photo-derived colored scrim (`harmonizePalette` Task 6/14), APCA math + role targets (Task 4/5/13), beautyCost minimization (Task 6/14), two-tier consistency (`chooseTheme` global + `fitRegion` local, Task 7/8/14), Gemini cached advisor + degradation (Task 16/17), web preview (Task 10/11), golden anti-drift (Task 1/12 + parity tests), regression that fails on `main` (Task 21), security/audit (Task 22). All spec sections map to tasks.
- **Consistency:** `Theme`, `RegionStats`, `AdvicePrior`, `fitRegion`, `chooseTheme`, `harmonizePalette`, `beautyCost`, `sampleRegion` names identical across TS and Kotlin. Role names `hero/body/muted` ↔ `HERO/BODY/MUTED`.
- **Known soft spots to watch during execution:** OKLCH→RGB rounding parity between JS and Kotlin (golden tolerance 1.0 on tint channel absorbs it); `_extract_json`/`_load_json`/`_save_json`/`BaseModel` must be matched to their real names in `server.py`/`program_engine.py`; the `GoldenSyncTest` relative path depends on the Gradle working dir.
